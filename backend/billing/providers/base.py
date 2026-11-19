from __future__ import annotations


class PaymentProvider:
    """Interface d'un fournisseur de paiement Mobile Money."""

    code = ""

    def initiate(self, payment) -> dict:
        """Lance le paiement. Retourne au minimum {"status": "pending", "instructions": "..."}."""
        raise NotImplementedError

    def parse_webhook(self, request) -> tuple[str, str]:
        """Extrait (reference, status) d'un webhook déjà authentifié (signature vérifiée).

        `status` vaut "confirmed" ou "failed". Lève ValueError si le corps est invalide.
        """
        raise NotImplementedError
