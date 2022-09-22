import pytest
from django.core.management import call_command

from ....models import (
    Plan,
    PlanSlug,
    Product,
    ProductCategory,
    ProductSlug,
    Step,
    Version,
)


@pytest.mark.django_db
def test_truncate_data(product_factory, step_factory):
    p1 = product_factory()
    p2 = product_factory()
    p3 = product_factory()

    step_factory(plan__version__product=p1)
    step_factory(plan__version__product=p2)
    step_factory(plan__version__product=p3)

    assert ProductCategory.objects.count() == 3
    assert ProductSlug.objects.count() == 3
    assert Product.objects.count() == 3
    assert Version.objects.count() == 3
    assert PlanSlug.objects.count() == 3
    assert Plan.objects.count() == 3
    assert Step.objects.count() == 3

    call_command("truncate_data")

    assert Product.objects.count() == 0
