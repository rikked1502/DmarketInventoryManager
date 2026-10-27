# DMarket Inventory Manager

A small desktop app for managing your [DMarket](https://dmarket.com) inventory through the official Trading API.
Supports **Counter-Strike 2** and **Team Fortress 2**, with light and dark themes.

## Features

- **Balance**: your available USD balance, always in the header.
- **Inventory**: your Steam and DMarket items in one list, with search and a *DMarket only* filter.
  Steam items are shown read-only; deposit them on dmarket.com first to sell them.
- **List for sale**: select items, start from DMarket's recommended price, see the lowest current listing,
  review, then confirm. Prices more than 30% below market are flagged before you confirm.
- **My offers**: change prices or remove listings in bulk, always behind a confirmation step.
- **Transactions**: your sales (with fee and net) and buy-order purchases in one list, filterable by Sold / Bought.

There is no automated trading: nothing changes on your account unless you confirm it.

## Setup

Requires Python 3.10+ on Windows, macOS or Linux (on Windows the window uses Microsoft Edge WebView2, which ships with Windows 10/11).

```bash
git clone https://github.com/<you>/DmarketInventoryManager.git
cd DmarketInventoryManager
pip install -r requirements.txt
```

1. On dmarket.com go to **Settings → Trading API** and generate a key pair.
2. Copy `.env.example` to `.env` and paste in your public and secret keys.
3. Run:

```bash
python app.py
```

If the keys are missing or wrong, the app shows a setup screen; fix `.env` and press **Try again**.

## Security

- Your keys are read from `.env` by the Python side only. They are never sent to the UI and never logged.
- Every request is signed locally with Ed25519, as the [DMarket API docs](https://docs.dmarket.com/v1/swagger.html) describe.
- `.env` is in `.gitignore`. Never commit it or share your secret key.

## Limitations

- **Transactions** only covers what the API exposes: completed sales (closed offers) and buy-order purchases
  (closed targets). Money deposits and withdrawals, and direct market purchases, are not included.
  History is account-wide across all games.
- No deposit or withdraw, buy orders management, or buying in this version.

## Project layout

| File | Purpose |
|------|---------|
| `app.py` | Opens the window and exposes the API methods to the UI |
| `dmarket.py` | Signed DMarket API client |
| `ui/` | The interface (HTML, CSS, JS) |
| `test_dmarket.py` | Tests for request signing and money handling: `python -m unittest` |

## License

MIT
