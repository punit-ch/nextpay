# NEXTPAY

NEXTPAY is an experimental AI-assisted commerce agent built for the
PayPal + AI hackathon.

## Current milestone

Milestone 1 provides:
- A sample product catalogue
- Server-side price and budget validation
- Purchase-plan creation
- Explicit user approval or rejection
- Automated API tests

## Current limitations

- Product data and prices are fictional sample data.
- No AI model is connected yet.
- No PayPal order is created or captured yet.
- Plans are stored in memory and disappear when the server restarts.
- This prototype is not production-ready.

## Requirements

- Python 3.11+
- Windows, macOS, or Linux

## Setup

Create and activate a virtual environment, then run:

    python -m pip install -r requirements.txt

Start the development server:

    python -m uvicorn app.main:app --reload

Open API documentation at:

    http://127.0.0.1:8000/docs

Run tests:

    python -m pytest -q

## Planned milestones

1. Secure backend and policy engine
2. AI intent extraction and explainable recommendations
3. PayPal sandbox order creation, approval, capture, and verification
4. Frontend purchase-review experience
5. Integration tests, deployment, demo recording, and submission

## Payment disclosure

The current milestone does not process payments. Future checkout
demonstrations will use PayPal sandbox credentials and test accounts.