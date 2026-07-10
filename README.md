# GHC Retention Analytics

## Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

## Frontend

```bash
npm install
npm run dev
```

Backend runs on `http://127.0.0.1:8000` and frontend on `http://127.0.0.1:5173`.

CSV columns required:

- `customer_id`
- `order_id`
- `created_at`
- `total_quantity`
- `discount_type`
