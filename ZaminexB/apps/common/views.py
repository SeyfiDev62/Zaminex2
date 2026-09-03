from rest_framework import status, permissions
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .throttles import PasswordResetRateThrottle, ResilientScopedRateThrottle

from .models import CompanySettings
from .serializers import CompanySettingsSerializer


class DistrictListView(APIView):
    """Active district names, newest hierarchy first.

    Kept for backwards compatibility: several screens still ask for a plain
    list of neighbourhood names. It now reads from `basics.District` (the
    Province → City → District hierarchy) and only falls back to the legacy
    flat table while a deployment has not been migrated yet.
    """

    def get(self, request):
        from apps.basics.models import District as HierarchyDistrict

        names = list(
            HierarchyDistrict.objects.filter(is_active=True)
            .order_by("city__province__sort_order", "city__sort_order", "sort_order")
            .values_list("display_name", flat=True)
        )
        if names:
            # De-duplicate while preserving order: the same neighbourhood name
            # may legitimately exist in two different cities.
            seen, unique = set(), []
            for name in names:
                if name not in seen:
                    seen.add(name)
                    unique.append(name)
            return Response(unique)

        legacy = CompanySettings.DistrictModel.objects.filter(is_active=True).order_by("name")
        return Response([d.name for d in legacy])


class DistrictManageView(APIView):
    """CRUD operations for districts (admin only)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """List all districts (including inactive)."""
        if getattr(request.user, "role", "") != "ADMIN":
            return Response({"detail": "فقط مدیر می‌تواند محله‌ها را مدیریت کند."}, status=status.HTTP_403_FORBIDDEN)
        
        from .serializers import DistrictSerializer
        districts = CompanySettings.DistrictModel.objects.all().order_by("name")
        return Response(DistrictSerializer(districts, many=True).data)

    def post(self, request):
        """Create a new district."""
        if getattr(request.user, "role", "") != "ADMIN":
            return Response({"detail": "فقط مدیر می‌تواند محله‌ها را مدیریت کند."}, status=status.HTTP_403_FORBIDDEN)
        
        from .serializers import DistrictSerializer
        name = request.data.get("name", "").strip()
        if not name:
            return Response({"detail": "نام محله الزامی است."}, status=status.HTTP_400_BAD_REQUEST)
        
        if CompanySettings.DistrictModel.objects.filter(name=name).exists():
            return Response({"detail": "این محله قبلاً ثبت شده است."}, status=status.HTTP_400_BAD_REQUEST)
        
        district = CompanySettings.DistrictModel.objects.create(name=name)
        return Response(DistrictSerializer(district).data, status=status.HTTP_201_CREATED)

    def delete(self, request, pk=None):
        """Delete a district."""
        if getattr(request.user, "role", "") != "ADMIN":
            return Response({"detail": "فقط مدیر می‌تواند محله‌ها را مدیریت کند."}, status=status.HTTP_403_FORBIDDEN)
        
        try:
            district = CompanySettings.DistrictModel.objects.get(pk=pk)
            district.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except CompanySettings.DistrictModel.DoesNotExist:
            return Response({"detail": "محله یافت نشد."}, status=status.HTTP_404_NOT_FOUND)

    def patch(self, request, pk=None):
        """Update a district."""
        if getattr(request.user, "role", "") != "ADMIN":
            return Response({"detail": "فقط مدیر می‌تواند محله‌ها را مدیریت کند."}, status=status.HTTP_403_FORBIDDEN)
        
        from .serializers import DistrictSerializer
        try:
            district = CompanySettings.DistrictModel.objects.get(pk=pk)
            serializer = DistrictSerializer(district, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)
        except CompanySettings.DistrictModel.DoesNotExist:
            return Response({"detail": "محله یافت نشد."}, status=status.HTTP_404_NOT_FOUND)


class CompanySettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        obj = CompanySettings.get_solo()
        return Response(CompanySettingsSerializer(obj).data)

    def _update(self, request):
        if getattr(request.user, "role", "") != "ADMIN":
            return Response(
                {"detail": "فقط مدیر می‌تواند اطلاعات شرکت را ویرایش کند."},
                status=status.HTTP_403_FORBIDDEN,
            )
        obj = CompanySettings.get_solo()
        serializer = CompanySettingsSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def patch(self, request):
        return self._update(request)

    def put(self, request):
        return self._update(request)

    
class PasswordResetRequestView(APIView):
    """Request password reset - creates notification for admins."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PasswordResetRateThrottle]

    def post(self, request):
        from django.contrib.auth import get_user_model
        from apps.notifications.models import Notification
        
        User = get_user_model()
        username = request.data.get("username", "").strip()
        
        if not username:
            return Response({"error": "نام کاربری الزامی است"}, status=400)
        
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            # Don't reveal if user exists or not
            return Response({"success": True, "message": "درخواست شما ثبت شد"})
        
        # Create notification for all admins
        admins = User.objects.filter(role="ADMIN")
        for admin in admins:
            Notification.objects.create(
                user=admin,
                type=Notification.NotificationType.PASSWORD_RESET_REQUEST,
                title="درخواست تغییر رمز عبور",
                message=f"کاربر {user.get_full_name() or user.username} درخواست تغییر رمز عبور خود را ارسال کرده است.",
                metadata={"requester_id": user.id, "requester_username": user.username}
            )
        
        return Response({"success": True, "message": "درخواست شما ثبت شد"})


class LoginStatsView(APIView):
    """Public, real-time counters shown on the login screen.

    The login page is public, so no authentication is required. Only aggregate
    counts are exposed — no rows, names, or personal data — which is safe to
    serve before the user signs in. Each figure is a single cheap, indexed
    COUNT query, so the login screen always reflects the live business state.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        from apps.accounts.models import ConsultantProfile, UserRole
        from apps.listings.models import Listing
        from apps.properties.models import Property

        return Response({
            # Non-archived properties currently under management.
            "totalProperties": Property.active_objects.count(),
            # Active consultant accounts.
            "activeConsultants": ConsultantProfile.objects.filter(
                is_active=True, user__role=UserRole.AGENT
            ).count(),
            # Deals closed: properties marked SOLD.
            "soldProperties": Property.objects.filter(
                status=Property.Status.SOLD
            ).count(),
            # Currently published listings.
            "activeListings": Listing.objects.filter(
                status=Listing.Status.ACTIVE
            ).count(),
        })


class AdminPasswordChangeView(APIView):
    """Admin changes user password - creates notifications."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, user_id=None):
        from django.contrib.auth import get_user_model
        from apps.notifications.models import Notification
        
        User = get_user_model()
        
        # Check if user is admin
        if getattr(request.user, "role", "") != "ADMIN":
            return Response({"error": "فقط مدیران می‌توانند رمز عبور را تغییر دهند"}, status=403)
        
        new_password = request.data.get("new_password", "")
        confirm_password = request.data.get("confirm_password", "")
        
        if not new_password or not confirm_password:
            return Response({"error": "رمز عبور و تکرار آن الزامی است"}, status=400)
        
        if new_password != confirm_password:
            return Response({"error": "رمز عبور و تکرار آن مطابقت ندارند"}, status=400)
        
        if len(new_password) < 8:
            return Response({"error": "رمز عبور باید حداقل ۸ کاراکتر باشد"}, status=400)
        
        try:
            target_user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({"error": "کاربر یافت نشد"}, status=404)
        
        # Change password
        target_user.set_password(new_password)
        target_user.save()
        
        # Create notification for all admins
        admins = User.objects.filter(role="ADMIN")
        admin_name = request.user.get_full_name() or request.user.username
        target_name = target_user.get_full_name() or target_user.username
        
        for admin in admins:
            Notification.objects.create(
                user=admin,
                type=Notification.NotificationType.PASSWORD_CHANGED,
                title="تغییر رمز عبور",
                message=f"مدیر {admin_name} رمز عبور کاربر {target_name} را تغییر داد.",
                metadata={"changed_user_id": target_user.id, "changed_by_id": request.user.id}
            )
        
        # Create notification for the target user
        Notification.objects.create(
            user=target_user,
            type=Notification.NotificationType.PASSWORD_CHANGED,
            title="تغییر رمز عبور",
            message=f"مدیر {admin_name} رمز عبور شما را تغییر داد. لطفاً با رمز جدید وارد شوید.",
            metadata={"changed_by_id": request.user.id}
        )
        
        return Response({"success": True, "message": "رمز عبور با موفقیت تغییر کرد"})


# ---------------------------------------------------------------------------
#  Geocoding proxy
# ---------------------------------------------------------------------------


class GeocodeView(APIView):
    """Resolve a Persian place name to coordinates on behalf of the map picker.

    ``GET /common/api/geocode/?q=…[&viewbox=w,n,e,s][&bounded=1]``

    The browser talks only to this endpoint, so the app's CSP needs no
    third-party host in ``connect-src`` and the upstream (public Nominatim, or
    a self-hosted one via ``GEOCODE_UPSTREAM``) is called from a single place
    that caches, paces itself and sends a descriptive ``User-Agent``.

    Response contract — the two failure modes are deliberately distinct, so
    the UI can tell "this place does not exist" from "the geocoder is down":

    * ``200`` + ``[]``      — the upstream answered; nothing matched.
    * ``200`` + ``[{…}]``   — hits, Nominatim-shaped plus a stable ``lat``/``lon``.
    * ``503``               — the upstream could not be reached or answered badly.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ResilientScopedRateThrottle]
    throttle_scope = "geocode"

    def get(self, request):
        from django.conf import settings

        from .geocode import GeocodeUnavailable, clean_viewbox, geocode

        query = (request.query_params.get("q") or "").strip()
        max_length = int(getattr(settings, "GEOCODE_MAX_QUERY_LENGTH", 200))
        if not query:
            return Response(
                {"detail": "عبارت جستجوی مکان نمی‌تواند خالی باشد."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(query) > max_length:
            return Response(
                {"detail": f"عبارت جستجو نباید بیشتر از {max_length} نویسه باشد."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        viewbox = clean_viewbox(request.query_params.get("viewbox"))
        if request.query_params.get("viewbox") and viewbox is None:
            return Response(
                {"detail": "محدودهٔ جغرافیایی معتبر نیست."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        bounded = request.query_params.get("bounded") in {"1", "true", "yes"}

        try:
            results = geocode(query, viewbox, bounded)
        except GeocodeUnavailable:
            # Not a client error: the place may well exist. The message must
            # say the *service* is unavailable, otherwise the operator keeps
            # re-typing an address that was never looked up.
            return Response(
                {"detail": "سرویس جستجوی مکان در دسترس نیست؛ کمی دیگر دوباره تلاش کنید."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(results)
