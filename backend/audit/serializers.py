from rest_framework import serializers

from .models import AuditEntry


class AuditEntrySerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditEntry
        fields = [
            "id", "user", "user_name", "action", "entity_type", "entity_id", "before", "after",
            "reason", "device_time", "ip_address", "created_at",
        ]
        read_only_fields = fields

    def get_user_name(self, obj) -> str:
        return obj.user.full_name if obj.user_id else ""
