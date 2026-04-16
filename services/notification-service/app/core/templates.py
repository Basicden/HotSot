"""HotSot Notification Service — Notification Templates.

Per-event-type message templates with i18n support.
Templates are resolved at dispatch time based on event_type.

Supported event types (V2 — 16 states):
  order.created, order.slot_reserved, order.payment_confirmed,
  order.in_prep, order.prep_done, order.quality_check,
  order.ready, order.on_shelf, order.arrived, order.picked,
  order.cancelled, order.expired, order.refunded,
  order.compensated, order.no_show, order.failed_qc

India i18n:
  - English (default)
  - Hindi (hi) — future: Tamil, Telugu, Marathi, Bengali
"""

import logging
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)

# Default language
DEFAULT_LANG = "en"


class NotificationTemplates:
    """Manages notification templates for all event types.

    Each template has:
      - title: Short heading for the notification
      - message: Body text with optional placeholders
      - placeholders: Dict keys that can be interpolated from extra_data

    Templates support multiple languages via lang parameter.
    """

    # ──────────────────────────────────────────────
    # English templates (V2 — 16 order states)
    # ──────────────────────────────────────────────
    TEMPLATES_EN: Dict[str, Dict[str, str]] = {
        # ── Order lifecycle ──
        "order.created": {
            "title": "Order Placed!",
            "message": "Your order #{order_id} has been placed successfully. Preparing your food soon!",
        },
        "order.slot_reserved": {
            "title": "Slot Reserved",
            "message": "Your pickup slot has been reserved for order #{order_id}. Please complete payment.",
        },
        "order.payment_confirmed": {
            "title": "Payment Confirmed",
            "message": "Payment received for order #{order_id}. The kitchen is being notified!",
        },
        "order.in_prep": {
            "title": "Being Prepared",
            "message": "Your order #{order_id} is now being prepared by the kitchen. ETA: {eta_minutes} min.",
        },
        "order.prep_done": {
            "title": "Almost Ready!",
            "message": "Your order #{order_id} is almost ready! Quality check in progress.",
        },
        "order.quality_check": {
            "title": "Quality Check",
            "message": "Your order #{order_id} is undergoing quality check. Almost there!",
        },
        "order.ready": {
            "title": "Order Ready!",
            "message": "Your order #{order_id} is ready! Heading to the pickup shelf now.",
        },
        "order.on_shelf": {
            "title": "Pick Up Now!",
            "message": "Your order #{order_id} is on shelf {shelf_id}. Please pick it up within {ttl_minutes} minutes.",
        },
        "order.arrived": {
            "title": "Welcome!",
            "message": "We detected your arrival for order #{order_id}. Your food is being prepared for handoff!",
        },
        "order.picked": {
            "title": "Order Picked Up!",
            "message": "Your order #{order_id} has been picked up. Enjoy your meal!",
        },
        # ── Terminal states ──
        "order.cancelled": {
            "title": "Order Cancelled",
            "message": "Your order #{order_id} has been cancelled. Refund will be processed if applicable.",
        },
        "order.expired": {
            "title": "Order Expired",
            "message": "Your order #{order_id} has expired on the shelf. Please contact support for refund.",
        },
        "order.refunded": {
            "title": "Refund Processed",
            "message": "A refund of Rs.{amount} has been processed for order #{order_id}. It may take 3-5 business days.",
        },
        "order.compensated": {
            "title": "Compensation Credited",
            "message": "Compensation of Rs.{amount} has been credited for order #{order_id} delay. We apologize for the inconvenience.",
        },
        "order.no_show": {
            "title": "Order Marked No-Show",
            "message": "Your order #{order_id} was not picked up in time. Please contact support if this is an error.",
        },
        "order.failed_qc": {
            "title": "Quality Issue Detected",
            "message": "Your order #{order_id} failed quality check and is being remade. No additional charges.",
        },
        # ── Payment events ──
        "payment.success": {
            "title": "Payment Successful!",
            "message": "Payment of Rs.{amount} for order #{order_id} was successful. Your order is being processed!",
        },
        "payment.failed": {
            "title": "Payment Failed",
            "message": "Payment for order #{order_id} failed. Please retry or use a different payment method.",
        },
        # ── Shelf events ──
        "shelf.expiry_warning": {
            "title": "Pickup Reminder!",
            "message": "Your order #{order_id} on shelf {shelf_id} expires in {ttl_minutes} minutes. Please pick it up soon!",
        },
        # ── Kitchen events ──
        "kitchen.new_order": {
            "title": "New Order",
            "message": "New order #{order_id} assigned to station {station}. Priority: {priority}.",
        },
        "kitchen.order_ready": {
            "title": "Order Ready for Pickup",
            "message": "Order #{order_id} is ready. Please place on shelf {shelf_id}.",
        },
        "kitchen.arrival_alert": {
            "title": "Customer Arriving!",
            "message": "Customer for order #{order_id} is arriving via {arrival_type}. Priority boost active!",
        },
        # ── Compensation events ──
        "compensation.evaluated": {
            "title": "Delay Compensation",
            "message": "We noticed a delay with order #{order_id}. Compensation of Rs.{amount} is being processed.",
        },
        "compensation.refund_initiated": {
            "title": "Refund Initiated",
            "message": "Refund of Rs.{amount} for order #{order_id} has been initiated via {refund_type}.",
        },
    }

    # ──────────────────────────────────────────────
    # Hindi templates (partial — key events)
    # ──────────────────────────────────────────────
    TEMPLATES_HI: Dict[str, Dict[str, str]] = {
        "order.created": {
            "title": "ऑर्डर हो गया!",
            "message": "आपका ऑर्डर #{order_id} सफलतापूर्वक हो गया है। जल्दी ही आपका खाना तैयार होगा!",
        },
        "order.ready": {
            "title": "ऑर्डर तैयार!",
            "message": "आपका ऑर्डर #{order_id} तैयार है! अब पिकअप शेल्फ पर जा रहा है।",
        },
        "order.on_shelf": {
            "title": "अभी उठाइये!",
            "message": "आपका ऑर्डर #{order_id} शेल्फ {shelf_id} पर है। कृपया {ttl_minutes} मिनट में उठा लें।",
        },
        "order.picked": {
            "title": "ऑर्डर उठा लिया!",
            "message": "आपका ऑर्डर #{order_id} उठा लिया गया है। भोजन का आनंद लीजिये!",
        },
        "order.cancelled": {
            "title": "ऑर्डर रद्द",
            "message": "आपका ऑर्डर #{order_id} रद्द कर दिया गया है। रिफंड प्रोसेस होगा यदि लागू हो।",
        },
        "order.refunded": {
            "title": "रिफंड हो गया",
            "message": "ऑर्डर #{order_id} के लिए Rs.{amount} का रिफंड प्रोसेस हो गया है।",
        },
        "order.compensated": {
            "title": "मुआवज़ा मिला",
            "message": "ऑर्डर #{order_id} की देरी के लिए Rs.{amount} मुआवज़ा दिया गया है।",
        },
        "order.arrived": {
            "title": "स्वागत है!",
            "message": "ऑर्डर #{order_id} के लिए आपकी आमद का पता चला। आपका खाना तैयार किया जा रहा है!",
        },
        "payment.success": {
            "title": "भुगतान सफल!",
            "message": "ऑर्डर #{order_id} के लिए Rs.{amount} का भुगतान हो गया। आपका ऑर्डर प्रोसेस हो रहा है!",
        },
        "payment.failed": {
            "title": "भुगतान विफल",
            "message": "ऑर्डर #{order_id} का भुगतान विफल हो गया। कृपया दोबारा प्रयास करें।",
        },
        "shelf.expiry_warning": {
            "title": "पिकअप रिमाइंडर!",
            "message": "आपका ऑर्डर #{order_id} शेल्फ {shelf_id} पर {ttl_minutes} मिनट में एक्सपायर होगा। कृपया जल्दी उठा लें!",
        },
    }

    def __init__(self):
        self._templates: Dict[str, Dict[str, Dict[str, str]]] = {
            "en": self.TEMPLATES_EN,
            "hi": self.TEMPLATES_HI,
        }

    def render(
        self,
        event_type: str,
        extra_data: Optional[Dict[str, Any]] = None,
        lang: str = DEFAULT_LANG,
    ) -> Tuple[str, str]:
        """Render a notification template for the given event type.

        Args:
            event_type: Event type key (e.g. 'order.created')
            extra_data: Dict with placeholder values (e.g. {'order_id': '123'})
            lang: Language code ('en', 'hi')

        Returns:
            Tuple of (title, message) with placeholders filled
        """
        extra_data = extra_data or {}

        # Look up template in requested language, fallback to English
        templates = self._templates.get(lang, self._templates[DEFAULT_LANG])
        template = templates.get(event_type)

        if not template:
            # Fallback to English
            template = self._templates[DEFAULT_LANG].get(event_type, {
                "title": f"Update: {event_type}",
                "message": f"Event: {event_type}. Order #{extra_data.get('order_id', '?')}.",
            })

        title = template["title"]
        message = template["message"]

        # Fill placeholders from extra_data
        # Add computed defaults
        render_data = {
            **extra_data,
            "eta_minutes": extra_data.get("eta_seconds", 0) // 60,
            "ttl_minutes": extra_data.get("ttl_seconds", 300) // 60,
            "amount": extra_data.get("amount", "0.00"),
        }

        try:
            title = title.format(**{k: v for k, v in render_data.items() if isinstance(v, (str, int, float))})
            message = message.format(**{k: v for k, v in render_data.items() if isinstance(v, (str, int, float))})
        except (KeyError, ValueError):
            # If a placeholder is missing, leave it as-is
            pass

        return title, message

    def add_template(self, event_type: str, title: str, message: str, lang: str = DEFAULT_LANG) -> None:
        """Register a custom template at runtime."""
        if lang not in self._templates:
            self._templates[lang] = {}
        self._templates[lang][event_type] = {"title": title, "message": message}

    def list_event_types(self, lang: str = DEFAULT_LANG) -> list:
        """List all registered event types for a language."""
        return list(self._templates.get(lang, {}).keys())
