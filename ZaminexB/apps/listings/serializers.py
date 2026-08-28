from django.db import transaction
from rest_framework import serializers

from apps.basics.models import Attribute, DealType
from apps.common.attribute_serializers import AttributeValuesMixin
from apps.common.metrics import (
    content_richness_score,
    engagement_heat_score,
    images_count_for_property,
    listing_marketing_metrics,
)
from .models import Listing, ListingAttributeValue
from apps.properties.models import Property
from apps.accounts.models import User

class PropertyMiniSerializer(serializers.ModelSerializer):
    district = serializers.CharField(source='neighborhood', read_only=True)
    image_url = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()

    def get_price(self, obj):
        """Derived from the property's sale listings (see metrics).

        List responses carry a precomputed ``effective_price_map`` in the
        serializer context (one query for the whole page, built by
        ``ListingViewSet.get_serializer_context``) so serializing a row never
        fires a per-row query. Detail responses fall back to the direct
        derivation for the single property.
        """
        price_map = self.context.get("effective_price_map")
        if price_map is not None:
            price = price_map.get(obj.pk, obj.price)
        else:
            from apps.common.metrics import effective_sale_price

            price = effective_sale_price(obj)
        return str(price) if price is not None else None


    class Meta:
        model = Property
        fields = ['id', 'title', 'district', 'price', 'area', 'floor', 'internal_code', 'image_url']

    def get_image_url(self, obj):
        if hasattr(obj, 'images') and obj.images.exists():
            first_image = obj.images.first()
            if first_image and first_image.image:
                request = self.context.get('request')
                if request:
                    return request.build_absolute_uri(first_image.image.url)
                return first_image.image.url
        return None

class UserMiniSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    mobile = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'name', 'email', 'mobile', 'role']

    def get_name(self, obj):
        return obj.get_full_name().strip() or obj.username

    def get_mobile(self, obj):
        profile = getattr(obj, 'consultant_profile', None)
        return profile.mobile if profile else None

class ListingSerializer(AttributeValuesMixin, serializers.ModelSerializer):
    # --- dynamic attributes (phase 3) --------------------------------------
    attribute_value_model = ListingAttributeValue
    attribute_owner_field = "listing"
    attribute_entity = Attribute.Entity.LISTING

    attributes = serializers.SerializerMethodField()
    attributeDetails = serializers.SerializerMethodField()

    # --- deal type & pricing -----------------------------------------------
    # Both live on the listing: one property can be advertised for sale and for
    # rent simultaneously, so neither belongs on the property record.
    dealType = serializers.PrimaryKeyRelatedField(
        source="deal_type",
        queryset=DealType.objects.all(),
        required=False,
        allow_null=True,
    )
    dealTypeName = serializers.CharField(
        source="deal_type.name", read_only=True, default=None
    )
    dealTypeDisplay = serializers.CharField(
        source="deal_type.display_name", read_only=True, default=None
    )
    salePrice = serializers.DecimalField(
        source="sale_price", max_digits=18, decimal_places=0,
        required=False, allow_null=True,
    )
    monthlyRent = serializers.DecimalField(
        source="monthly_rent", max_digits=18, decimal_places=0,
        required=False, allow_null=True,
    )
    priceDetails = serializers.JSONField(source="price_details", required=False)

    property_detail = PropertyMiniSerializer(source='property', read_only=True)
    created_by_detail = UserMiniSerializer(source='created_by', read_only=True)
    assigned_to_detail = UserMiniSerializer(source='assigned_to', read_only=True)
    
    channels = serializers.SerializerMethodField()
    score = serializers.SerializerMethodField()
    views = serializers.SerializerMethodField()
    # Single wire name for the channel (camelCase, like the rest of the API).
    # Writable + choice-validated on create/update; the model keeps its
    # snake_case column via `source`.
    publishChannel = serializers.ChoiceField(
        choices=Listing.PublishChannel.choices,
        source="publish_channel",
    )
    effectiveExposureDays = serializers.SerializerMethodField()
    delegationIndicator = serializers.SerializerMethodField()
    isBurnedListing = serializers.SerializerMethodField()
    generatedHighProbLeads = serializers.SerializerMethodField()
    contentRichnessScore = serializers.SerializerMethodField()
    engagementHeatScore = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = [
            'id', 'title', 'description', 'status', 'publishChannel',
            'start_date', 'end_date', 'assigned_to', 'created_by',
            'priority', 'is_featured', 'created_at', 'updated_at',
            'property', 'property_detail', 'created_by_detail', 'assigned_to_detail',
            'channels', 'score', 'views',
            'effectiveExposureDays', 'delegationIndicator', 'isBurnedListing',
            'generatedHighProbLeads', 'contentRichnessScore', 'engagementHeatScore',
            'dealType', 'dealTypeName', 'dealTypeDisplay',
            'salePrice', 'deposit', 'monthlyRent', 'priceDetails',
            'attributes', 'attributeDetails',
        ]
        read_only_fields = ['created_by', 'created_at', 'updated_at']

    def get_channels(self, obj):
        return [obj.publish_channel] if obj.publish_channel else []

    def _listing_metrics(self, obj):
        if not hasattr(self, "_listing_metrics_cache"):
            setattr(self, "_listing_metrics_cache", {})
        cache = self._listing_metrics_cache
        if obj.pk not in cache:
            cache[obj.pk] = listing_marketing_metrics(obj)
        return cache[obj.pk]

    def get_score(self, obj):
        richness = self._listing_metrics(obj).get("contentRichnessScore") or 0
        return int(round((richness / 5) * 100))

    def get_views(self, obj):
        return self._listing_metrics(obj).get("engagementHeatScore") or 0

    def get_effectiveExposureDays(self, obj):
        return self._listing_metrics(obj).get("effectiveExposureDays")

    def get_delegationIndicator(self, obj):
        return self._listing_metrics(obj).get("delegationIndicator")

    def get_isBurnedListing(self, obj):
        return self._listing_metrics(obj).get("isBurnedListing")

    def get_generatedHighProbLeads(self, obj):
        return self._listing_metrics(obj).get("generatedHighProbLeads")

    def get_contentRichnessScore(self, obj):
        return self._listing_metrics(obj).get("contentRichnessScore")

    def get_engagementHeatScore(self, obj):
        return self._listing_metrics(obj).get("engagementHeatScore")

    # -- persistence --------------------------------------------------------

    def validate(self, attrs):
        from apps.common.access import can_access_property

        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        prop = attrs.get("property")
        if prop is None and self.instance is not None:
            prop = self.instance.property
        if user and prop is not None and not can_access_property(user, prop):
            raise serializers.ValidationError({"property": "شما به این ملک دسترسی ندارید."})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        payload = self._pop_attribute_payload()
        instance = super().create(validated_data)
        self._save_attribute_values(instance, payload or {})
        self._validate_required_attributes(
            instance, instance.deal_type, "attribute_links"
        )
        return instance

    @transaction.atomic
    def update(self, instance, validated_data):
        payload = self._pop_attribute_payload()
        instance = super().update(instance, validated_data)
        if payload is not None:
            self._save_attribute_values(instance, payload)
        self._validate_required_attributes(
            instance, instance.deal_type, "attribute_links"
        )
        return instance


# Fields the list serializer drops from the full payload (Phase 1). The full
# serializer is sized for one listing at a time (the detail page and the edit
# wizard); on a 1000-row list these fields also caused ~8 queries per row
# (the per-row marketing metrics, the user profiles and the property price
# derivation). What the list screens read stays:
#   * the card/table columns (title, status, channels, dates, price fields,
#     deal type, the property's title + first photo, the assigned consultant);
#   * ``score`` and ``views`` — the two KPIs of the listings page. They are
#     overridden below to compute from the relations the list view prefetches,
#     so they cost zero queries per row.
_LISTING_LIST_EXCLUDED = {
    "description",
    "attributes",
    "attributeDetails",
    "created_by_detail",
    "priceDetails",
    "effectiveExposureDays",
    "delegationIndicator",
    "isBurnedListing",
    "generatedHighProbLeads",
    "contentRichnessScore",
    "engagementHeatScore",
}


class ListingListSerializer(ListingSerializer):
    """Slim read-only serializer for list responses (Phase 1).

    See ``_LISTING_LIST_EXCLUDED`` for the dropped fields. Read-only by
    construction: it is only ever used for ``list`` responses, so no write
    path can pick it up.
    """

    class Meta(ListingSerializer.Meta):
        fields = [
            field
            for field in ListingSerializer.Meta.fields
            if field not in _LISTING_LIST_EXCLUDED
        ]

    # The base implementation routes both KPIs through
    # ``listing_marketing_metrics``, which also counts high-probability
    # follow-ups — one query per row on a list. The list view prefetches
    # ``property__images``, ``property__followups`` and ``property__tasks``,
    # so compute the two KPIs straight from those cached relations instead.
    def get_score(self, obj):
        prop = obj.property
        img_count = images_count_for_property(prop) if prop else 0
        return int(round((content_richness_score(obj, img_count) / 5) * 100))

    def get_views(self, obj):
        prop = obj.property
        return engagement_heat_score(prop) if prop else 0
