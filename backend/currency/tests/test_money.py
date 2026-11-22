"""Tests des calculs monétaires purs : aucune base de données, aucun float."""

from decimal import Decimal

import pytest

from currency.money import (
    CDF,
    USD,
    MoneyError,
    UnsupportedCurrency,
    compute_payment,
    convert_minor,
    format_minor,
    from_minor,
    get_currency,
    to_minor,
)

RATE = Decimal("2800")


# --- to_minor / from_minor / format_minor -------------------------------------------------------
@pytest.mark.parametrize(
    "value,currency,expected",
    [
        ("12.50", USD, 1250),
        ("12.5", "USD", 1250),
        ("0.01", USD, 1),
        ("0", USD, 0),
        ("12.505", USD, 1251),  # .5 -> vers le haut
        ("12.504", USD, 1250),
        ("0.005", USD, 1),
        ("-0.005", USD, -1),  # ROUND_HALF_UP : loin de zéro
        ("12500", CDF, 12500),
        ("12500.5", CDF, 12501),
        ("12500.49", CDF, 12500),
        (Decimal("1234.567"), USD, 123457),
        (7, CDF, 7),
        (" 5.00 ", USD, 500),
        ("99999999999999999999.99", USD, 9999999999999999999999),  # très grand
        ("0.0000001", USD, 0),  # très petit
    ],
)
def test_to_minor(value, currency, expected):
    assert to_minor(value, currency) == expected


def test_to_minor_rejects_float_and_garbage():
    with pytest.raises(TypeError):
        to_minor(12.5, USD)
    with pytest.raises(TypeError):
        to_minor(True, USD)
    for bad in ("abc", "", "NaN", "Infinity"):
        with pytest.raises(MoneyError):
            to_minor(bad, USD)
    with pytest.raises(UnsupportedCurrency):
        to_minor("1", "EUR")


def test_from_minor():
    assert from_minor(1250, USD) == Decimal("12.50")
    assert str(from_minor(1250, USD)) == "12.50"
    assert str(from_minor(5, USD)) == "0.05"
    assert str(from_minor(0, USD)) == "0.00"
    assert str(from_minor(12500, CDF)) == "12500"
    assert from_minor(-1250, "USD") == Decimal("-12.50")
    assert from_minor(9999999999999999999999, USD) == Decimal("99999999999999999999.99")
    assert isinstance(from_minor(1, USD), Decimal)
    with pytest.raises(TypeError):
        from_minor(12.5, USD)
    with pytest.raises(TypeError):
        from_minor(Decimal("1"), USD)


@pytest.mark.parametrize("minor", [0, 1, 99, 100, 123456789, -5, -99999])
@pytest.mark.parametrize("currency", [CDF, USD])
def test_roundtrip(minor, currency):
    assert to_minor(from_minor(minor, currency), currency) == minor


def test_format_minor():
    assert format_minor(12500, CDF) == "12 500 FC"
    assert format_minor(0, CDF) == "0 FC"
    assert format_minor(999, "CDF") == "999 FC"
    assert format_minor(1234567890, CDF) == "1 234 567 890 FC"
    assert format_minor(1250, USD) == "$12.50"
    assert format_minor(5, USD) == "$0.05"
    assert format_minor(0, USD) == "$0.00"
    assert format_minor(123450, USD) == "$1,234.50"
    assert format_minor(-1250, USD) == "-$12.50"
    assert format_minor(-12500, CDF) == "-12 500 FC"


def test_currency_registry():
    assert get_currency("CDF") is CDF and get_currency(USD) is USD
    assert CDF.decimals == 0 and USD.decimals == 2
    with pytest.raises(UnsupportedCurrency):
        get_currency("EUR")
    with pytest.raises(UnsupportedCurrency):
        get_currency(None)


# --- convert_minor -----------------------------------------------------------------------------
def test_convert_exact():
    r = convert_minor(280000, CDF, USD, RATE)  # 280 000 FC = 100 USD
    assert (r.amount_minor, r.exact, r.rounding_diff_minor) == (10000, Decimal(10000), Decimal(0))
    r = convert_minor(1000, USD, CDF, RATE)  # 10 USD
    assert (r.amount_minor, r.rounding_diff_minor) == (28000, Decimal(0))


def test_convert_same_currency_is_identity():
    r = convert_minor(1234, USD, USD, "0")  # le taux est sans objet
    assert r.amount_minor == 1234 and r.rounding_diff_minor == 0


def test_convert_cdf_to_usd_rounds_half_up_with_diff():
    # 1 425 FC à 2 850 = 0,5 USD = 50 cts exactement
    assert convert_minor(1425, CDF, USD, "2850").amount_minor == 50
    # 14 FC à 2800 = 0,005 USD = 0,5 ct -> 1 ct (half up), écart +0,5
    r = convert_minor(14, CDF, USD, RATE)
    assert r.exact == Decimal("0.5") and r.amount_minor == 1 and r.rounding_diff_minor == Decimal("0.5")
    # 13 FC = 0,464 ct -> 0, écart -0,4642857...
    r = convert_minor(13, CDF, USD, RATE)
    assert r.amount_minor == 0 and r.rounding_diff_minor < 0
    assert r.amount_minor - r.exact == r.rounding_diff_minor


def test_convert_usd_to_cdf_fractional_rate():
    # 1 USD à 2 850,5 = 2 850,5 FC -> 2 851 (half up), écart +0,5
    r = convert_minor(100, USD, CDF, "2850.5")
    assert r.exact == Decimal("2850.5") and r.amount_minor == 2851
    assert r.rounding_diff_minor == Decimal("0.5")
    # 0,01 USD = 28,505 FC -> 29
    r = convert_minor(1, USD, CDF, Decimal("2850.5"))
    assert r.exact == Decimal("28.505") and r.amount_minor == 29


def test_convert_rate_2850_5_cdf_to_usd():
    r = convert_minor(285050, CDF, USD, "2850.5")  # exactement 100 USD
    assert r.amount_minor == 10000 and r.rounding_diff_minor == 0


def test_convert_zero_and_negative():
    assert convert_minor(0, CDF, USD, RATE).amount_minor == 0
    assert convert_minor(0, USD, CDF, RATE).amount_minor == 0
    assert convert_minor(-14, CDF, USD, RATE).amount_minor == -1  # half up loin de zéro


def test_convert_huge_amount_is_exact():
    big = 10**30  # 10^30 FC
    r = convert_minor(big, CDF, USD, "2800")
    assert r.amount_minor == (big * 100) // 2800 + (1 if (big * 100) % 2800 * 2 >= 2800 else 0)
    # retour : pas de perte de précision sur un entier gigantesque
    r2 = convert_minor(10**30, USD, CDF, "2800")  # 10^28 USD = 2,8 x 10^31 FC
    assert r2.amount_minor == 28 * 10**30 and r2.rounding_diff_minor == 0
    assert convert_minor(10**25, USD, CDF, "1").amount_minor == 10**23


@pytest.mark.parametrize("rate", ["0.0001", "0.5", "1", "1000000000", "9999999999.9999"])
def test_convert_extreme_rates(rate):
    r = convert_minor(1_000_000, CDF, USD, rate)
    assert isinstance(r.amount_minor, int)
    assert r.amount_minor - r.exact == r.rounding_diff_minor
    assert abs(r.rounding_diff_minor) <= Decimal("0.5")
    r = convert_minor(12345, USD, CDF, rate)
    assert abs(r.rounding_diff_minor) <= Decimal("0.5")


def test_convert_extreme_rate_values():
    assert convert_minor(1, CDF, USD, "0.0001").amount_minor == 1_000_000  # 1 FC = 10 000 USD
    assert convert_minor(100, USD, CDF, "9999999999.9999").amount_minor == 10000000000  # arrondi


def test_convert_rejects_bad_input():
    for bad in ("0", "-1", "abc", "NaN", "Infinity", Decimal("-5")):
        with pytest.raises(MoneyError):
            convert_minor(100, CDF, USD, bad)
    with pytest.raises(TypeError):
        convert_minor(100, CDF, USD, 2800.0)
    with pytest.raises(TypeError):
        convert_minor(1.5, CDF, USD, RATE)
    with pytest.raises(UnsupportedCurrency):
        convert_minor(100, CDF, "EUR", RATE)


def test_no_float_anywhere():
    r = convert_minor(12345, CDF, USD, "2850.5")
    assert not isinstance(r.amount_minor, float)
    assert isinstance(r.exact, Decimal) and isinstance(r.rounding_diff_minor, Decimal)
    p = compute_payment(100, USD, [(280000, CDF)], RATE, CDF)
    for value in (p.paid_minor, p.remaining_minor, p.change_minor, p.overpaid_minor):
        assert isinstance(value, int)
    assert isinstance(p.paid_rounding_diff_minor, Decimal)


# --- compute_payment ---------------------------------------------------------------------------
def test_payment_exact_cdf():
    p = compute_payment(50000, CDF, [(50000, CDF)], RATE, CDF)
    assert (p.paid_minor, p.remaining_minor, p.change_minor, p.overpaid_minor) == (50000, 0, 0, 0)
    assert p.is_fully_paid and p.change_rounding_diff_minor == 0


def test_payment_exact_mixed():
    # vente 100 USD : 50 USD + 140 000 FC (= 50 USD)
    p = compute_payment(10000, USD, [(5000, USD), (140000, CDF)], RATE, USD)
    assert p.paid_minor == 10000 and p.remaining_minor == 0 and p.change_minor == 0


def test_payment_partial():
    p = compute_payment(10000, USD, [(2000, USD)], RATE, USD)
    assert p.paid_minor == 2000 and p.remaining_minor == 8000 and not p.is_fully_paid
    assert p.change_minor == 0
    p = compute_payment(100000, CDF, [(28000, CDF), (1000, USD)], RATE, CDF)  # 28 000 FC + 10 USD (28 000 FC)
    assert p.paid_minor == 56000 and p.remaining_minor == 44000


def test_payment_no_payment():
    p = compute_payment(1000, CDF, [], RATE, CDF)
    assert p.paid_minor == 0 and p.remaining_minor == 1000 and p.change_minor == 0
    p = compute_payment(0, CDF, [], RATE, CDF)
    assert p.is_fully_paid


def test_payment_overpaid_cdf_change_in_usd():
    # vente 25 000 FC, client donne 50 000 FC, rendu en USD : excédent 25 000 FC = 8,92857 USD
    p = compute_payment(25000, CDF, [(50000, CDF)], RATE, USD)
    assert p.overpaid_minor == 25000 and p.remaining_minor == 0
    assert p.change_minor == 893 and p.change_currency == "USD"
    assert abs(p.change_rounding_diff_minor - Decimal("0.142857142857")) < Decimal("1e-11")


def test_payment_overpaid_usd_change_in_cdf():
    # vente 8 USD, client donne 10 USD, rendu en CDF : 2 USD = 5 600 FC
    p = compute_payment(800, USD, [(1000, USD)], RATE, CDF)
    assert p.change_minor == 5600 and p.change_currency == "CDF" and p.change_rounding_diff_minor == 0
    # taux 2850.5 : 2 USD = 5 701 FC exact
    p = compute_payment(800, USD, [(1000, USD)], "2850.5", CDF)
    assert p.change_minor == 5701
    # excédent 1 ct -> 28,505 FC -> 29, écart +0,495
    p = compute_payment(799, USD, [(800, USD)], "2850.5", CDF)
    assert p.change_minor == 29 and p.change_rounding_diff_minor == Decimal("0.495")


def test_payment_overpaid_same_currency_change():
    p = compute_payment(7000, CDF, [(10000, CDF)], RATE, CDF)
    assert p.change_minor == 3000 and p.change_rounding_diff_minor == 0


def test_payment_multi_currency_sum_rounded_once():
    # Deux paiements de 14 FC chacun valent 0,5 ct + 0,5 ct = 1 ct exactement (et non 1+1 = 2)
    p = compute_payment(1, USD, [(14, CDF), (14, CDF)], RATE, USD)
    assert p.paid_minor == 1 and p.paid_rounding_diff_minor == 0 and p.is_fully_paid
    # Sens inverse : vente en CDF, 3 paiements de 1 ct à 28,505 FC l'un = 85,515 -> 86
    p = compute_payment(86, CDF, [(1, USD)] * 3, "2850.5", CDF)
    assert p.paid_minor == 86 and p.paid_rounding_diff_minor == Decimal("0.485")


def test_payment_three_currency_entries_mixed_order():
    p = compute_payment(
        total_minor=500000,
        total_currency=CDF,
        payments=[(100000, CDF), (50, USD), (1000, USD)],  # 100 000 + 0,5$ (1 400) + 10$ (28 000)
        rate_cdf_per_usd=RATE,
        change_currency=CDF,
    )
    assert p.paid_minor == 129400 and p.remaining_minor == 500000 - 129400


def test_payment_extreme_rates():
    p = compute_payment(100, USD, [(1_000_000_000, CDF)], "9999999999.9999", USD)  # 1 Md FC ~ 0,1 USD
    assert p.paid_minor == 10 and p.remaining_minor == 90
    p = compute_payment(100, USD, [(1, CDF)], "0.0001", USD)  # 1 FC = 10 000 USD
    assert p.paid_minor == 1_000_000 and p.overpaid_minor == 999_900 and p.change_minor == 999_900


def test_payment_tiny_overpayment_rounds_change_to_zero_with_diff():
    # excédent 1 FC rendu en USD à 2 800 = 0,0357 ct -> 0 ; l'écart est tracé
    p = compute_payment(1000, CDF, [(1001, CDF)], RATE, USD)
    assert p.overpaid_minor == 1 and p.change_minor == 0
    assert p.change_rounding_diff_minor < 0


def test_payment_huge_amounts():
    total = 10**20
    p = compute_payment(total, CDF, [(total * 2, CDF)], RATE, CDF)
    assert p.change_minor == total


def test_payment_accepts_str_rate_and_currency_codes():
    p = compute_payment(1000, "USD", [(1000, "USD")], "2850.5", "CDF")
    assert p.is_fully_paid


def test_payment_rejects_bad_input():
    with pytest.raises(MoneyError):
        compute_payment(-1, CDF, [], RATE, CDF)
    with pytest.raises(MoneyError):
        compute_payment(100, CDF, [(-5, CDF)], RATE, CDF)
    with pytest.raises(TypeError):
        compute_payment(100, CDF, [(5.5, CDF)], RATE, CDF)
    with pytest.raises(TypeError):
        compute_payment(100.0, CDF, [], RATE, CDF)
    with pytest.raises(MoneyError):
        compute_payment(100, CDF, [(5, USD)], "0", CDF)
    with pytest.raises(UnsupportedCurrency):
        compute_payment(100, CDF, [(5, "EUR")], RATE, CDF)
