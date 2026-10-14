import argparse

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.base import auth_user
from app.db.models.accounts import AdminAudit, AppAccount
from app.db.session import session_context


def promote_admin(session: Session, user_id: str, reason: str) -> bool:
    if not user_id.strip() or len(user_id) > 256 or not reason.strip() or len(reason) > 500:
        raise ValueError("A bounded existing user ID and audit reason are required")
    with session.begin():
        account = session.scalar(
            select(AppAccount).where(AppAccount.user_id == user_id).with_for_update()
        )
        auth_id = session.scalar(select(auth_user.c.id).where(auth_user.c.id == user_id))
        if account is None or auth_id is None:
            raise ValueError("User must have an account created by a verified app login")
        if account.status != "active":
            raise ValueError("A suspended account cannot be promoted")
        if account.role == "admin":
            return False
        account.role = "admin"
        session.add(
            AdminAudit(
                actor_user_id=user_id,
                action="initial_admin_assignment",
                target_id=user_id,
                reason=reason.strip(),
                before_json={"role": "user"},
                after_json={"role": "admin"},
            )
        )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("command", choices=["promote-admin"])
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    try:
        with session_context() as session:
            changed = promote_admin(session, args.user_id, args.reason)
    except ValueError as error:
        parser.error(str(error))
    except SQLAlchemyError:
        parser.exit(1, "Database operation failed; verify the database and migration setup\n")
    print("Administrator assigned" if changed else "Account is already an administrator")


if __name__ == "__main__":
    main()
