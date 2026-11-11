"""Access rules: package ACL, DocAcl, owner-only writes."""

from __future__ import annotations

from dataclasses import dataclass

from evidence_core.ids import is_allowed
from evidence_core.models.api import DocAclItem, Package

from . import store
from .auth import Caller
from .deps import Deps
from .http import forbidden, not_found


def require_package(deps: Deps, caller: Caller, package_id: str) -> Package:
    pkg = store.get_package(deps, package_id)
    if pkg is None:
        raise not_found(f"Package {package_id} not found.")
    if not is_allowed(caller.principals, pkg.allowed_principals):
        raise forbidden("You don't have access to this package.")
    return pkg


def require_package_owner(deps: Deps, caller: Caller, package_id: str) -> Package:
    pkg = require_package(deps, caller, package_id)
    if pkg.owner_sub != caller.sub:
        raise forbidden("Only the package owner can do this.")
    return pkg


@dataclass(frozen=True)
class DocAccess:
    acl: DocAclItem
    is_owner: bool

    @property
    def package_id(self) -> str:
        return self.acl.package_id


def require_doc(deps: Deps, caller: Caller, doc_id: str) -> DocAccess:
    acl = store.get_doc_acl(deps, doc_id)
    if acl is None:
        raise not_found(f"Document {doc_id} not found.")
    if not is_allowed(caller.principals, acl.allowed_principals):
        raise forbidden("You don't have access to this document.")
    return DocAccess(acl=acl, is_owner=acl.owner_sub == caller.sub)


def can_read_doc(deps: Deps, caller: Caller, doc_id: str) -> bool:
    """The DocAcl check of ``require_doc`` as a predicate, for composers that scan a package's documents and must
    skip (not reveal) the ones this caller may not read."""
    acl = store.get_doc_acl(deps, doc_id)
    return acl is not None and is_allowed(caller.principals, acl.allowed_principals)
