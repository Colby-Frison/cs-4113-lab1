
from dataclasses import dataclass


@dataclass
class Ticket:
    ticket_id: str
    description: str
    priority: str = "medium"
    status: str = "new"
    assigned_team: str = ""


def classify_ticket(ticket):
    """Determine which team should handle the ticket."""
    description = ticket.description.lower()

    if "password" in description or "login" in description:
        ticket.assigned_team = "account_support"
    elif "payment" in description or "billing" in description:
        ticket.assigned_team = "billing_support"
    else:
        ticket.assigned_team = "general_support"

    ticket.status = "classified"
    return ticket


def assign_ticket(ticket):
    """Assign a classified ticket to its support team."""
    if ticket.status != "classified":
        raise ValueError("Ticket must be classified before assignment")

    ticket.status = "assigned"
    return ticket


def resolve_ticket(ticket):
    """Mark an assigned ticket as resolved."""
    if ticket.status != "assigned":
        raise ValueError("Ticket must be assigned before resolution")

    ticket.status = "resolved"
    return ticket


def process_ticket(ticket):
    """Process a ticket through all three stages."""
    classify_ticket(ticket)
    assign_ticket(ticket)
    resolve_ticket(ticket)
    return ticket
