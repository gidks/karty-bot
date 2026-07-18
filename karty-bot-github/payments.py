"""Оплата картой через ЮKassa (прямой API, работает для самозанятых).
SDK ЮKassa синхронный — оборачиваем в asyncio.to_thread, чтобы не блокировать бота.
Чеки: для самозанятых фискализацию делает сам владелец через «Мой налог»."""

import asyncio
import logging
import uuid

import config

log = logging.getLogger(__name__)


def available() -> bool:
    return config.yookassa_available()


def _configure() -> None:
    from yookassa import Configuration

    Configuration.account_id = config.YOOKASSA_SHOP_ID
    Configuration.secret_key = config.YOOKASSA_SECRET_KEY


async def create(user_id: int, plan_key: str) -> tuple[str, str]:
    """Создаёт платёж. Возвращает (payment_id, ссылка_на_оплату)."""
    plan = config.PLANS[plan_key]

    def _sync() -> tuple[str, str]:
        from yookassa import Payment

        _configure()
        payment = Payment.create(
            {
                "amount": {"value": f"{plan['rub']:.2f}", "currency": "RUB"},
                "confirmation": {
                    "type": "redirect",
                    "return_url": f"https://t.me/{config.BOT_USERNAME}",
                },
                "capture": True,
                "description": f"«Карты Не Врут» — {plan['title']} (18+, развлекательный формат)",
                "metadata": {"user_id": str(user_id), "plan": plan_key},
            },
            str(uuid.uuid4()),
        )
        return payment.id, payment.confirmation.confirmation_url

    return await asyncio.to_thread(_sync)


async def status(payment_id: str) -> str:
    """Статус платежа: pending / waiting_for_capture / succeeded / canceled."""

    def _sync() -> str:
        from yookassa import Payment

        _configure()
        return Payment.find_one(payment_id).status

    return await asyncio.to_thread(_sync)
