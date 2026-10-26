"""Import every module's models so `Base.metadata` is complete.

Alembic's env.py and the test-suite import this module; application code should import
models from their owning module instead.
"""

from affilio.db.base import Base
from affilio.modules.accounts import models as accounts_models
from affilio.modules.identity import models as identity_models
from affilio.modules.media import models as media_models
from affilio.modules.procurement import models as procurement_models
from affilio.modules.production import models as production_models
from affilio.modules.products import models as products_models

__all__ = [
    "Base",
    "accounts_models",
    "identity_models",
    "media_models",
    "procurement_models",
    "production_models",
    "products_models",
]
