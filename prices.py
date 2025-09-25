"""
prices.py — helper per ottenere prezzi FX (valute fiat) e crypto con semplici funzioni.
- FX provider: Frankfurter (ECB) o exchangerate.host
- Crypto provider: CoinGecko

Esempi veloci:
    from prices import get_fx_rate, get_crypto_price

    # Cambio USD→EUR (da Frankfurter)
    rate, asof = get_fx_rate("USD", "EUR")
    print("USD/EUR:", rate, "data:", asof)

    # Prezzo BTC in EUR (da CoinGecko)
    price = get_crypto_price("bitcoin", "eur")
    print("BTC/EUR:", price)
"""
from urllib.request import urlopen
from urllib.parse import urlencode
import json
from datetime import datetime

class PriceError(Exception):
    """Errore generico per problemi di prezzo/API."""

def _fetch_json(url: str) -> dict:
    try:
        with urlopen(url, timeout=12) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)
    except Exception as e:
        raise PriceError(f"Errore nella chiamata a {url}: {e}") from e

def get_fx_rate(base: str = "USD", quote: str = "EUR", provider: str = "frankfurter"):
    """
    Ritorna (tasso, data_stringa) per il cambio base→quote.
    provider: "frankfurter" (default) oppure "exchangerate.host".
    """
    base = base.upper().strip()
    quote = quote.upper().strip()

    if provider == "frankfurter":
        # https://api.frankfurter.app/latest?from=USD&to=EUR
        url = "https://api.frankfurter.app/latest?" + urlencode({"from": base, "to": quote})
        js = _fetch_json(url)
        rates = js.get("rates", {})
        if quote not in rates:
            raise PriceError(f"Tasso {base}->{quote} non trovato nella risposta Frankfurter.")
        rate = float(rates[quote])
        asof = js.get("date")
        return rate, asof

    elif provider == "exchangerate.host":
        # https://api.exchangerate.host/latest?base=USD&symbols=EUR
        url = "https://api.exchangerate.host/latest?" + urlencode({"base": base, "symbols": quote})
        js = _fetch_json(url)
        rates = js.get("rates", {})
        if quote not in rates:
            raise PriceError(f"Tasso {base}->{quote} non trovato nella risposta exchangerate.host.")
        rate = float(rates[quote])
        # exchangerate.host include anche "date"
        asof = js.get("date")
        return rate, asof

    else:
        raise ValueError('provider deve essere "frankfurter" o "exchangerate.host"')

def get_crypto_price(coin_id: str = "bitcoin", vs: str = "usd"):
    """
    Ritorna il prezzo corrente (float) di una coin crypto rispetto a una valuta fiat.
    Usa CoinGecko /simple/price (no API key).
    Esempi:
        get_crypto_price("bitcoin", "eur")
        get_crypto_price("ethereum", "usd")
    """
    coin_id = coin_id.strip().lower()
    vs = vs.strip().lower()
    # https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=eur
    url = "https://api.coingecko.com/api/v3/simple/price?" + urlencode({"ids": coin_id, "vs_currencies": vs})
    js = _fetch_json(url)
    if coin_id not in js or vs not in js[coin_id]:
        raise PriceError(f"Prezzo {coin_id}/{vs} non trovato nella risposta CoinGecko.")
    return float(js[coin_id][vs])

if __name__ == "__main__":
    # Piccolo CLI: esegui
    #   python prices.py fx USD EUR
    #   python prices.py crypto bitcoin eur
    import sys
    args = sys.argv[1:]
    try:
        if len(args) >= 3 and args[0] == "fx":
            r, d = get_fx_rate(args[1], args[2])
            print(f"{args[1].upper()}/{args[2].upper()} = {r} (data {d})")
        elif len(args) >= 3 and args[0] == "crypto":
            p = get_crypto_price(args[1], args[2])
            print(f"{args[1]}/{args[2]} = {p}")
        else:
            print("Uso:\n  python prices.py fx USD EUR\n  python prices.py crypto bitcoin eur")
    except Exception as e:
        print("Errore:", e)
