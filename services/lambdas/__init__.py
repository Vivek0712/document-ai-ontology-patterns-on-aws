"""Lambda handlers. Package name in the deployment asset: ``lambdas``.

Each handler lives in ``services/lambdas/<name>/handler.py`` with ``def handler(event, context)``
and is deployed as function ``rp-<name with - for _>`` with handler string
``lambdas.<name>.handler.handler``. 
"""
