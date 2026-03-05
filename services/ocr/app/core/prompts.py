RECEIPT_OCR_PROMPT = """
Return ONLY valid JSON. No markdown, no explanations.
If the image is a valid receipt, return the Result Schema.
If the image does not look like a receipt, return the Error Schema.

[Result Schema]
{
  "result": {
    "items": [{"name": string, "unit_price": int, "quantity": int, "amount": int}],
    "total_amount": int | null,
    "paid_amount": int | null,
    "discount_amount": int | null
  }
}

[Error Schema]
{"error": {"code": "INVALID_IMAGE_TYPE", "message": "입력된 이미지가 유효한 영수증 형식이 아닙니다."}}

Rules:
1. Extract ONLY "Menu/Product" line items.
2. EXCLUDE tax-related metadata (부가세, VAT, 공급가액 등) from "items".
3. 'total_amount': The amount before discounts (합계금액/총금액).
4. 'paid_amount': The final amount charged (결제금액/승인금액).
5. 'discount_amount': Sum of all reductions.
   - Include negative amounts (e.g., -3,100) under menu items.
   - Include fields labeled '할인금액', '서비스', 'D.C'.
   - Return as a positive integer.
6. If an item has 0 price (and is not a main product), EXCLUDE it.
""".strip()