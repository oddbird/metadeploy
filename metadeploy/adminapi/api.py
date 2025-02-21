from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from rest_framework import generics, permissions, serializers, status, viewsets
from rest_framework.response import Response
from sfdo_template_helpers.admin.serializers import AdminAPISerializer
from sfdo_template_helpers.admin.views import AdminAPIViewSet as BaseAdminAPIViewSet

from metadeploy.adminapi.translations import update_all_translations
from metadeploy.api import models
from metadeploy.api.models import SUPPORTED_ORG_TYPES, Plan
from metadeploy.api.serializers import get_from_data_or_instance


class StrictDjangoModelPermissions(permissions.DjangoModelPermissions):
    """
    Extends `DjangoModelPermissions` to enforce the "view" permission as well
    """

    perms_map = permissions.DjangoModelPermissions.perms_map | {
        "GET": ["%(app_label)s.view_%(model_name)s"],
    }


class AdminAPIViewSet(BaseAdminAPIViewSet):
    permission_classes = [permissions.IsAdminUser, StrictDjangoModelPermissions]


class ExcludeSiteSerializer(AdminAPISerializer):
    class Meta:
        exclude = ("site",)


class ProductSerializer(AdminAPISerializer):
    title = serializers.CharField()
    tags = serializers.ListField(
        child=serializers.CharField(), allow_empty=True, required=False
    )
    short_description = serializers.CharField()
    description = serializers.CharField()
    click_through_agreement = serializers.CharField()
    error_message = serializers.CharField()
    slug = serializers.CharField()

    class Meta:
        fields = "__all__"


class ProductFilter(filters.FilterSet):
    class Meta:
        model = models.Product
        exclude = ("image",)


class ProductViewSet(AdminAPIViewSet):
    model_name = "Product"
    serializer_base = ProductSerializer
    filterset_class = ProductFilter


class ProductSlugViewSet(AdminAPIViewSet):
    model_name = "ProductSlug"


class PlanStepSerializer(serializers.ModelSerializer):
    name = serializers.CharField()
    description = serializers.CharField(required=False)

    class Meta:
        model = models.Step
        exclude = ("id", "plan")


class PlanSerializer(AdminAPISerializer):
    steps = PlanStepSerializer(many=True, required=False)
    title = serializers.CharField()
    preflight_message_additional = serializers.CharField(
        required=False, allow_blank=True
    )
    post_install_message_additional = serializers.CharField(
        required=False, allow_blank=True
    )

    class Meta:
        exclude = ["calculated_average_duration"]
        extra_kwargs = {"plan_template": {"required": False}}

    def validate(self, data):
        """
        Check that restricted plans only support persistent orgs.
        Also checks that plan with the same version does not have more
        than one primary or secondary plan.
        """

        visible_to = get_from_data_or_instance(self.instance, data, "visible_to")
        supported_orgs = get_from_data_or_instance(
            self.instance,
            data,
            "supported_orgs",
            default=SUPPORTED_ORG_TYPES.Persistent,
        )
        if visible_to and supported_orgs != SUPPORTED_ORG_TYPES.Persistent:
            raise serializers.ValidationError(
                {
                    "supported_orgs": _(
                        'Restricted plans (with a "visible to" AllowedList) can only support persistent org types.'
                    )
                }
            )
        if self.context["request"].method == "POST":
            tier = (
                get_from_data_or_instance(self.instance, data, "tier")
                or Plan.Tier.primary
            )
            plan_version = get_from_data_or_instance(self.instance, data, "version")
            if tier == Plan.Tier.primary and plan_version.primary_plan:
                raise serializers.ValidationError(
                    {
                        "version": _(
                            "You must not have more than one primary plan per version"
                        )
                    }
                )

            elif tier == Plan.Tier.secondary and plan_version.secondary_plan:
                raise serializers.ValidationError(
                    {
                        "version": _(
                            "You must not have more than one secondary plan per version"
                        )
                    }
                )

        return data

    def create(self, validated_data):
        steps = validated_data.pop("steps") or []
        plan = self.Meta.model.objects.create(**validated_data)
        for step_data in steps:
            plan.steps.create(**step_data)
        return plan

    def update(self, instance, validated_data):
        if "steps" in validated_data:
            raise serializers.ValidationError(_("Updating steps not supported."))
        validated_data.pop("steps", None)
        return super().update(instance, validated_data)


class PlanTemplateSerializer(AdminAPISerializer):
    preflight_message = serializers.CharField(required=False, allow_blank=True)
    post_install_message = serializers.CharField(required=False, allow_blank=True)
    error_message = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        fields = "__all__"


class PlanTemplateViewSet(AdminAPIViewSet):
    model_name = "PlanTemplate"
    serializer_base = PlanTemplateSerializer


class PlanFilter(filters.FilterSet):
    class Meta:
        model = models.Plan
        exclude = ("preflight_checks",)


class PlanViewSet(AdminAPIViewSet):
    model_name = "Plan"
    serializer_base = PlanSerializer
    filterset_class = PlanFilter


class PlanSlugViewSet(AdminAPIViewSet):
    model_name = "PlanSlug"


class VersionViewSet(AdminAPIViewSet):
    model_name = "Version"


class ProductCategoryViewSet(AdminAPIViewSet):
    model_name = "ProductCategory"
    serializer_base = ExcludeSiteSerializer


class AllowedListViewSet(AdminAPIViewSet):
    model_name = "AllowedList"
    serializer_base = ExcludeSiteSerializer

    # `AdminAPIViewSet` includes all fields by default, but `org_id` causes an exception
    # because its an `ArrayField`, so we exclude it for now
    filterset_fields = ("title", "description", "list_for_allowed_by_orgs")


class AllowedListOrgSerializer(AdminAPISerializer):
    created_by = serializers.StringRelatedField()


class AllowedListOrgViewSet(AdminAPIViewSet):
    model_name = "AllowedListOrg"
    serializer_base = AllowedListOrgSerializer


class SiteProfileSerializer(serializers.ModelSerializer):
    # django-parler fields need to be declared explicitly
    name = serializers.CharField()
    company_name = serializers.CharField()
    welcome_text = serializers.CharField()
    master_agreement = serializers.CharField()
    copyright_notice = serializers.CharField()

    class Meta:
        model = models.SiteProfile
        exclude = ("id", "site")


class SiteProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = SiteProfileSerializer
    permission_classes = AdminAPIViewSet.permission_classes
    queryset = models.SiteProfile.objects  # Required by `permission_classes`

    def get_object(self):
        """Admin API users should only be able to edit the current SiteProfile"""
        return get_object_or_404(models.SiteProfile, site_id=self.request.site_id)


class TranslationViewSet(viewsets.ViewSet):
    """Facilitates adding/updating translated text.

    PATCH /admin/rest/translations/es

    {
        "context": {
            "slug": {
                "message": "En español",
                "description": "Spanish translation for slug in this context"
            }
        }
    }
    """

    model_name = "Translation"
    permission_classes = AdminAPIViewSet.permission_classes
    queryset = models.Translation.objects  # Required by `permission_classes`

    def partial_update(self, request, pk=None):
        # Add or update a Translation record for each message
        lang = pk
        if lang not in (lang for lang, label in settings.LANGUAGES):
            return Response("", status=status.HTTP_404_NOT_FOUND)
        for context, messages in request.data.items():
            for slug, message in messages.items():
                record, created = models.Translation.objects.get_or_create(
                    lang=lang,
                    context=context,
                    slug=slug,
                )
                record.text = message["message"]
                record.save()

        update_all_translations.delay(lang, request.site_id)
        return Response({})
