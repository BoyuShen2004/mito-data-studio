"""Phase 5 review-loop exceptions.

Their own module because ``models.py`` raises them and ``services.py`` catches
them: defining them in either would create an import cycle the moment the other
needs them.
"""

from __future__ import annotations


class ImmutableReviewError(Exception):
    """An attempt to edit a ``ReviewRecord`` after it was written.

    Not a ``ValidationError``: this is not a user-input problem to be shown
    next to a form field, it is a caller doing something the data model does
    not permit at all.
    """
