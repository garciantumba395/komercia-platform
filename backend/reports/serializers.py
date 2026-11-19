"""Serializers de documentation (drf-spectacular) et de validation des paramètres."""

import datetime

from rest_framework import serializers

from . import services


def _parse_date(name: str, raw: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        raise serializers.ValidationError({name: "Date invalide (format attendu : AAAA-MM-JJ)."}) from None


def parse_daily_params(params) -> tuple[datetime.date]:
    raw = params.get("date")
    return (_parse_date("date", raw) if raw else services.today(),)


def parse_summary_params(params) -> tuple[datetime.date, datetime.date]:
    period = params.get("period") or "week"
    end = services.today()
    if period == "week":
        return end - datetime.timedelta(days=6), end
    if period == "month":
        return end.replace(day=1), end
    if period != "custom":
        raise serializers.ValidationError({"period": "Valeur invalide (week, month ou custom)."})
    errors = {n: "Obligatoire avec period=custom." for n in ("date_from", "date_to") if not params.get(n)}
    if errors:
        raise serializers.ValidationError(errors)
    date_from = _parse_date("date_from", params["date_from"])
    date_to = _parse_date("date_to", params["date_to"])
    if date_from > date_to:
        raise serializers.ValidationError({"date_from": "La date de début doit précéder la date de fin."})
    if (date_to - date_from).days + 1 > services.MAX_RANGE_DAYS:
        raise serializers.ValidationError({"date_to": f"Période limitée à {services.MAX_RANGE_DAYS} jours."})
    return date_from, date_to


class MoneyByCurrencySerializer(serializers.Serializer):
    CDF = serializers.IntegerField()
    USD = serializers.IntegerField()


class TopProductSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    name = serializers.CharField()
    quantity = serializers.CharField()
    revenue_minor = serializers.IntegerField()
    currency = serializers.ChoiceField(choices=["CDF", "USD"])


class LowStockSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    name = serializers.CharField()
    store = serializers.UUIDField()
    quantity = serializers.CharField()
    min_stock = serializers.CharField()


class DailyReportSerializer(serializers.Serializer):
    date = serializers.DateField()
    store = serializers.UUIDField(allow_null=True)
    sales_count = serializers.IntegerField()
    revenue = MoneyByCurrencySerializer()
    cash_collected = MoneyByCurrencySerializer()
    credit_given = MoneyByCurrencySerializer()
    estimated_margin = MoneyByCurrencySerializer()
    top_products = TopProductSerializer(many=True)
    low_stock = LowStockSerializer(many=True)
    debts = MoneyByCurrencySerializer()
    cancelled_count = serializers.IntegerField()


class ByDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    sales_count = serializers.IntegerField()
    revenue = MoneyByCurrencySerializer()


class ByStoreSerializer(serializers.Serializer):
    store = serializers.UUIDField()
    name = serializers.CharField()
    sales_count = serializers.IntegerField()
    revenue = MoneyByCurrencySerializer()


class SummaryReportSerializer(serializers.Serializer):
    sales_count = serializers.IntegerField()
    revenue = MoneyByCurrencySerializer()
    cash_collected = MoneyByCurrencySerializer()
    credit_given = MoneyByCurrencySerializer()
    estimated_margin = MoneyByCurrencySerializer()
    by_day = ByDaySerializer(many=True)
    top_products = TopProductSerializer(many=True)
    by_store = ByStoreSerializer(many=True)
