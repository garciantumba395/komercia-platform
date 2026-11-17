"""Logique JavaScript pure (montants, rôles, téléphone) exécutée avec Node, sans navigateur."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static" / "web"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js indisponible")

HARNESS = """
const vm = require('vm'), fs = require('fs');
const noop = () => {};
const win = {
  localStorage: {}, location: { search: '', replace: noop }, matchMedia: () => ({ matches: false }),
};
const doc = {
  addEventListener: noop, body: { dataset: {} }, getElementById: () => null,
  createElement: () => ({ appendChild: noop, setAttribute: noop, addEventListener: noop }),
  createTextNode: (t) => t,
};
const ctx = vm.createContext({
  window: win, document: doc, URLSearchParams, Intl, BigInt, console, setTimeout, clearTimeout,
});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
const K = win.Komersia;
const arg = (a) => (typeof a === 'string' && a.startsWith('rate:') ? K.rateScaled(a.slice(5)) : a);
const out = JSON.parse(process.argv[2]).map(([fn, args]) => fn.split('.').reduce((o, k) => o[k], K)(...args.map(arg)));
console.log(JSON.stringify(out));
"""


def run(calls):
    result = subprocess.run(
        [NODE, "-e", HARNESS, str(STATIC / "app.js"), json.dumps(calls)],
        capture_output=True, text=True, timeout=30, check=True)
    return json.loads(result.stdout)


def test_js_files_are_syntactically_valid():
    for name in ("app.js", "pages.js", "pos.js"):
        subprocess.run([NODE, "--check", str(STATIC / name)], check=True, capture_output=True, timeout=30)


def test_amount_formatting_and_parsing():
    got = run([
        ["fmt", [1234567, "CDF"]], ["fmt", [12345, "USD"]], ["fmt", [-500, "USD"]],
        ["parseAmount", ["12 500", "CDF"]], ["parseAmount", ["12,50", "USD"]],
        ["parseAmount", ["12.5", "CDF"]], ["parseAmount", ["1.234", "USD"]], ["parseAmount", ["", "CDF"]],
        ["parseAmount", ["abc", "USD"]], ["toInput", [1250, "USD"]],
    ])
    assert got[0].replace(" ", " ").replace("\xa0", " ") == "1 234 567 FC"
    assert got[1] == "$123.45" and got[2] == "-$5.00"
    assert got[3:] == [12500, 1250, None, None, 0, None, "12.50"]


def test_mixed_payment_rounds_once_in_sale_currency():
    # 2850 FC/$ : 1000 FC + 1,00 $ = 1,35 $ (rounded once) ; en francs 3 850 FC
    rs = "rate:2850.0000"
    got = run([
        ["paidInCurrency", [1000, 100, "USD", rs]], ["paidInCurrency", [1000, 100, "CDF", rs]],
        ["convert", [3500, "CDF", "USD", rs]], ["convert", [123, "USD", "CDF", rs]],
    ])
    assert got == [135, 3850, 123, 3506]


def test_phone_normalisation_and_role_rules():
    got = run([
        ["normalizePhone", ["0990 000 001"]], ["normalizePhone", ["243990000001"]],
        ["normalizePhone", ["+243990000001"]],
        ["canWrite.cancel", ["owner"]], ["canWrite.cancel", ["cashier"]], ["canWrite.cancel", ["storekeeper"]],
        ["canWrite.products", ["manager"]], ["canWrite.products", ["storekeeper"]],
        ["canWrite.stock", ["storekeeper"]],
    ])
    assert got == ["+243990000001"] * 3 + [True, False, False, True, False, True]
