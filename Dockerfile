FROM python:3.10.0b3

ARG BUILD_ENV
WORKDIR /app

# System setup:
RUN apt-get update \
  && apt-get install -y \
    redis-tools \
    --no-install-recommends \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONPATH /app
ENV DJANGO_SETTINGS_MODULE config.settings.production

# declaring necessary node and yarn versions
ENV NODE_VERSION 16.13.1
# installing node
COPY ./utility/install_node.sh utility/install_node.sh
RUN /bin/sh utility/install_node.sh
# declaring necessary node and yarn versions
ENV YARN_VERSION 1.22.17
# installing yarn
COPY ./utility/install_yarn.sh utility/install_yarn.sh
RUN /bin/sh utility/install_yarn.sh
# Install sfdx
RUN npm install --global sfdx-cli --ignore-scripts

# installing python related dependencies with pip
COPY ./requirements requirements
RUN pip install --no-cache-dir --upgrade pip pip-tools \
    && pip install --no-cache-dir -r requirements/prod.txt
RUN if [ "${BUILD_ENV}" = "development" ] ; then \
    pip install --no-cache-dir -r requirements/dev.txt; \
    fi

COPY ./package.json package.json
COPY ./yarn.lock yarn.lock
RUN yarn install --check-files

COPY . /app

# Avoid building prod assets in development
RUN if [ "${BUILD_ENV}" = "production" ] ; then yarn prod ; else mkdir -p dist/prod ; fi

RUN DATABASE_URL="" \
  DB_ENCRYPTION_KEY="Ul-OySkEawSxUc7Ck13Twu2109IzIFh54C1WXO9KAFE=" \
  DJANGO_HASHID_SALT="" \
  DJANGO_SECRET_KEY="sample secret key" \
  GITHUB_TOKEN="sample token" \
  SFDX_CLIENT_SECRET="sample secret" \
  SFDX_CLIENT_CALLBACK_URL="sample callback" \
  SFDX_CLIENT_ID="sample id" \
  python manage.py collectstatic --noinput

CMD /app/utility/start_server.sh
