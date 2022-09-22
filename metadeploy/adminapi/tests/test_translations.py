import pytest

from metadeploy.adminapi.translations import update_all_translations
from metadeploy.multitenancy import override_current_site_id


@pytest.mark.django_db
class TestUpdateAllTranslations:
    def test_ok(self, plan_factory, step_factory, translation_factory):
        plan = plan_factory(version__product__title="Example")
        product = plan.version.product
        step1 = step_factory(name="Foo", plan=plan)
        step2 = step_factory(name="Install Example 1.0", plan=plan)

        translation_factory(
            lang="es", context="example:product", slug="title", text="Ejemplo"
        )
        translation_factory(
            lang="es",
            context="example:steps",
            slug="Install {product} {version}",
            text="Instalar {product} {version}",
        )
        translation_factory(lang="es", context="example:steps", slug="Foo", text="Fú")

        update_all_translations("es", site_id=1)

        product.set_current_language("es")
        assert product.title == "Ejemplo"

        step1.set_current_language("es")
        assert step1.name == "Fú"

        step2.set_current_language("es")
        assert step2.name == "Instalar Example 1.0"

    def test_multi_tenancy(self, mocker, plan_factory, translation_factory, extra_site):
        default_site_plan = plan_factory(version__product__title="Example")
        default_site_product = default_site_plan.version.product
        with override_current_site_id(extra_site.id):
            plan = plan_factory(version__product__title="Example")
            product = plan.version.product
            translation_factory(
                lang="es", context="example:product", slug="title", text="Ejemplo"
            )

        update_all_translations("es", site_id=extra_site.id)

        product.set_current_language("es")
        assert product.title == "Ejemplo"

        default_site_product.set_current_language("es")
        assert (
            default_site_product.title == "Example"
        ), "Expected translations on the extra Site to not affect the default Site"
