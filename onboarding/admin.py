"""Django admin registration - the human-facing ops surface for tickets.

Gives the mandatory "view tickets / documents / timeline, update status"
admin capability without custom UI code.
"""

from django.contrib import admin

from .models import (
    DeadLetter,
    Document,
    Notification,
    RawEmail,
    Ticket,
    TicketEvent,
    ValidationResult,
)


class TicketEventInline(admin.TabularInline):
    model = TicketEvent
    extra = 0
    can_delete = False
    readonly_fields = ("event_type", "message", "payload", "created_at")
    ordering = ("created_at", "id")


class DocumentInline(admin.TabularInline):
    model = Document
    extra = 0
    can_delete = False
    readonly_fields = ("filename", "content_type", "size_bytes", "sha256", "status")
    show_change_link = True


class ValidationResultInline(admin.TabularInline):
    model = ValidationResult
    extra = 0
    can_delete = False
    readonly_fields = ("check_name", "passed", "detail", "created_at")


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "ticket_ref",
        "status",
        "applicant_name",
        "applicant_email",
        "applicant_phone",
        "sla_due_at",
        "sla_breached",
        "created_at",
    )
    list_filter = ("status", "sla_breached", "created_at")
    search_fields = (
        "ticket_ref",
        "applicant_name",
        "applicant_email",
        "applicant_phone",
    )
    readonly_fields = ("ticket_ref", "raw_email", "created_at", "updated_at")
    inlines = (DocumentInline, ValidationResultInline, TicketEventInline)
    date_hierarchy = "created_at"


@admin.register(RawEmail)
class RawEmailAdmin(admin.ModelAdmin):
    list_display = ("id", "message_id", "from_addr", "subject", "received_at")
    search_fields = ("message_id", "from_addr", "subject", "content_hash")
    readonly_fields = ("content_hash", "created_at", "updated_at")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "filename", "content_type", "status", "size_bytes")
    list_filter = ("content_type", "status")
    search_fields = ("filename", "sha256", "ticket__ticket_ref")


@admin.register(TicketEvent)
class TicketEventAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "event_type", "message", "created_at")
    list_filter = ("event_type",)
    search_fields = ("ticket__ticket_ref", "message")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "template", "to_addr", "status", "sent_at")
    list_filter = ("status", "channel")
    search_fields = ("to_addr", "ticket__ticket_ref")


@admin.register(ValidationResult)
class ValidationResultAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "check_name", "passed", "created_at")
    list_filter = ("passed", "check_name")
    search_fields = ("ticket__ticket_ref",)


@admin.register(DeadLetter)
class DeadLetterAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "task_name", "reprocessed", "created_at")
    list_filter = ("reprocessed", "task_name")
    search_fields = ("ticket__ticket_ref", "task_name")
