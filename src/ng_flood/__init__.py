"""Google's flood forecast for a Nigerian coordinate, labelled with its NIPOST
postcode area. Expected failures come back as values, never exceptions."""

from .assess import Assessment, Riverine
from .client import Client, Failure

__all__ = ["Assessment", "Client", "Failure", "Riverine"]
