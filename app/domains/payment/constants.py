"""
=========================================================
Homez OS

File : app/domains/payment/constants.py

2026-09-10 Phase 7(HOMEZ_USER_OPERATION_SETTINGS.md 4·5·11번 —
"카드·PayPal·계좌이체·가상계좌를 결제수단 종류로 설계한다", "카드번호
+CVC 원문을 저장하지 않는다") — 결제수단 종류 고정 상수.
=========================================================
"""


class PaymentMethodType:

    CARD = "CARD"
    PAYPAL = "PAYPAL"
    BANK_TRANSFER = "BANK_TRANSFER"
    VIRTUAL_ACCOUNT = "VIRTUAL_ACCOUNT"

    ALL = (CARD, PAYPAL, BANK_TRANSFER, VIRTUAL_ACCOUNT)

    LABELS_KO = {
        CARD: "카드",
        PAYPAL: "PayPal",
        BANK_TRANSFER: "계좌이체",
        VIRTUAL_ACCOUNT: "가상계좌",
    }


__all__ = ["PaymentMethodType"]
