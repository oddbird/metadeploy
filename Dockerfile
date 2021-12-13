FROM --platform=linux/amd64 python:3.10.0b4

# System setup:
RUN apt-get update \
  && apt-get install -y \
    redis-tools \
    --no-install-recommends \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

ARG BUILD_ENV
RUN mkdir /app
# declaring necessary node and yarn versions
ENV NODE_VERSION 14.17.4
# installing node
COPY ./utility/install_node.sh /app/utility/install_node.sh
RUN /bin/sh /app/utility/install_node.sh
# declaring necessary node and yarn versions
ENV YARN_VERSION 1.22.5
# installing yarn
COPY ./utility/install_yarn.sh /app/utility/install_yarn.sh
RUN /bin/sh /app/utility/install_yarn.sh
# # installing sfdx
COPY ./utility/install_sfdx.sh /app/utility/install_sfdx.sh
RUN /bin/sh /app/utility/install_sfdx.sh
# installing python related dependencies with pip
COPY ./requirements /app/requirements
RUN pip install --no-cache-dir --upgrade pip pip-tools \
    && pip install --no-cache-dir -r /app/requirements/prod.txt
RUN if [ "${BUILD_ENV}" = "development" ] ; then \
    pip install --no-cache-dir -r /app/requirements/dev.txt; \
    fi
COPY ./package.json /app/package.json
COPY ./yarn.lock /app/yarn.lock
WORKDIR /app
RUN yarn install --check-files
# copying rest of working directory to /app folder
COPY . /app

# Avoid building prod assets in development
RUN if [ "${BUILD_ENV}" = "production" ] ; then yarn prod ; else mkdir -p dist/prod ; fi

ENV PYTHONUNBUFFERED 1
# Don't write .pyc files
ENV PYTHONDONTWRITEBYTECODE 1
ENV DJANGO_SETTINGS_MODULE config.settings.local

RUN DATABASE_URL="" \
  DB_ENCRYPTION_KEY="Ul-OySkEawSxUc7Ck13Twu2109IzIFh54C1WXO9KAFE=" \
  DJANGO_HASHID_SALT="" \
  DJANGO_SECRET_KEY="sample secret key" \
  GITHUB_TOKEN="sample token" \
  SFDX_CLIENT_SECRET="sample secret" \
  SFDX_CLIENT_CALLBACK_URL="sample callback" \
  SFDX_CLIENT_ID="sample id" \
  python manage.py collectstatic --noinput
