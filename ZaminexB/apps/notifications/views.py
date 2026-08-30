from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView


# Phase 5: the notification bell and the ticket unread badge are polled by
# the SPA (30 s, often from several tabs). The responses are tiny and
# per-user, so a very short per-user TTL coalesces that polling into at most
# one query per user per window instead of one per tab per tick. A user's own
# write (mark read) drops their key immediately, so their next poll is fresh;
# the TTL is the backstop for writes that skip the invalidation. Fail-open:
# a cache problem falls back to the direct query.
POLL_TTL = 10  # seconds (roadmap: 5-10 s)


def _notifications_poll_key(user) -> str:
    from apps.common import cache_utils

    return cache_utils.make_key("poll", "notifications", user.pk)


class NotificationListView(APIView):
    """Get notifications for the current user."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from apps.common import cache_utils
        from .models import Notification

        key = _notifications_poll_key(request.user)
        cached = cache_utils.cache_get(key)
        if isinstance(cached, dict):
            return Response(cached)

        # Get notifications for the current user
        notifications = Notification.objects.filter(user=request.user)[:50]

        data = []
        for notif in notifications:
            data.append({
                "id": notif.id,
                "type": notif.type,
                "typeLabel": notif.get_type_display(),
                "title": notif.title,
                "message": notif.message,
                "isRead": notif.is_read,
                "createdAt": notif.created_at.isoformat(),
                "metadata": notif.metadata,
            })

        # Count unread
        unread_count = Notification.objects.filter(user=request.user, is_read=False).count()

        payload = {
            "notifications": data,
            "unreadCount": unread_count,
        }
        cache_utils.cache_set(key, payload, POLL_TTL)
        return Response(payload)


class NotificationMarkReadView(APIView):
    """Mark a notification as read."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk=None):
        from apps.common import cache_utils
        from .models import Notification

        try:
            notif = Notification.objects.get(pk=pk, user=request.user)
            notif.is_read = True
            notif.save()
            # Phase 5: drop this user's cached poll so their next bell poll
            # is immediately fresh (the TTL is only the backstop).
            cache_utils.cache_delete(_notifications_poll_key(request.user))
            return Response({"success": True})
        except Notification.DoesNotExist:
            return Response({"error": "اعلان یافت نشد"}, status=404)
