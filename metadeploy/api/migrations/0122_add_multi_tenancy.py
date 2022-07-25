# Generated by Django 3.2.13 on 2022-06-22 23:23

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sites", "0002_alter_domain_unique"),
        ("api", "0121_add_product_tags"),
    ]

    operations = [
        migrations.AddField(
            model_name="allowedlist",
            name="site",
            field=models.ForeignKey(
                default=1, on_delete=django.db.models.deletion.CASCADE, to="sites.site"
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="clickthroughagreement",
            name="site",
            field=models.ForeignKey(
                default=1, on_delete=django.db.models.deletion.CASCADE, to="sites.site"
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="job",
            name="site",
            field=models.ForeignKey(
                default=1, on_delete=django.db.models.deletion.CASCADE, to="sites.site"
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="productcategory",
            name="site",
            field=models.ForeignKey(
                default=1, on_delete=django.db.models.deletion.CASCADE, to="sites.site"
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="translation",
            name="site",
            field=models.ForeignKey(
                default=1, on_delete=django.db.models.deletion.CASCADE, to="sites.site"
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="allowedlist",
            name="title",
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name="productslug",
            name="slug",
            field=models.SlugField(),
        ),
        migrations.AlterUniqueTogether(
            name="allowedlist",
            unique_together={("site", "title")},
        ),
        migrations.AlterUniqueTogether(
            name="translation",
            unique_together={("site", "context", "slug", "lang")},
        ),
        migrations.CreateModel(
            name="Token",
            fields=[
                (
                    "key",
                    models.CharField(
                        max_length=40,
                        primary_key=True,
                        serialize=False,
                        verbose_name="Key",
                    ),
                ),
                (
                    "created",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created"),
                ),
                (
                    "site",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="sites.site"
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "unique_together": {("user", "site")},
            },
        ),
    ]