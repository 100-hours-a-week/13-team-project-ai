# app/core/prompts.py

RECEIPT_OCR_PROMPT = """
Return ONLY valid JSON. No markdown, no explanations.
If the image is a valid receipt, return the Result Schema.
If the image does not look like a receipt, return the Error Schema.

[Result Schema]
{
  "result": {
    "items": [{"name": string, "unit_price": int, "quantity": int, "amount": int}],
    "total_amount": int | null,
    "paid_amount": int | null
  }
}

[Error Schema]
{"error": {"code": "INVALID_IMAGE_TYPE", "message": "입력된 이미지가 유효한 영수증 형식이 아닙니다."}}

Rules:
1. Extract ONLY "Menu/Product" line items.
2. EXCLUDE tax-related or subtotal metadata from the "items" list.
   - Never include: 부가세, 부가가치세, VAT, 과세물품가액, 면세물품가액, 공급가액, 봉사료, 합계.
3. Logical Filtering: Only include items that represent an actual dish, product, or service purchased.
4. If an item has 0 quantity or seems to be a tax calculation (like 10% of a subtotal), EXCLUDE it.
5. 'total_amount' is the final amount to be paid (합계/결제금액).
6. 'paid_amount' is the actual amount charged to the card or paid in cash.
""".strip()


