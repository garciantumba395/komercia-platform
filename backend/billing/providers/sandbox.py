import json

from .base import PaymentProvider


class SandboxProvider(PaymentProvider):
    """Fournisseur de test : le paiement reste `pending` jusqu'à confirmation manuelle."""

    code = "sandbox"

    def initiate(self, payment) -> dict:
        return {
            "status": "pending",
            "instructions": (
                f"Mode test : confirmez le paiement {payment.reference} via "
                "POST /api/v1/billing/sandbox/confirm/."
            ),
        }

    def parse_webhook(self, request) -> tuple[str, str]:
        try:
            body = json.loads(request.body or b"{}")
            reference, status = body["reference"], body["status"]
        except (ValueError, KeyError, TypeError):
            raise ValueError("Corps de webhook invalide.") from None
        if not isinstance(reference, str) or status not in ("confirmed", "failed"):
            raise ValueError("Référence ou statut invalide.")
        return reference, status
