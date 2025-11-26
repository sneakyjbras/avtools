from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from avtools.eam.position import EAMPosition

# --- Base --------------------------------------------------------------------


class Base(DeclarativeBase):
    """Base for EAM ORM models."""

    pass


# --- ORM ---------------------------------------------------------------------


class EAMPositionORM(Base):
    """EAM position node (positions form a parent/child tree in EAM)."""

    __tablename__ = "eam_positions"
    __table_args__ = (
        Index("ix_eam_positions_parentasset", "parentasset"),
        Index("ix_eam_positions_status", "assetstatus_display"),
        Index("ix_eam_positions_eqclass_category", "eqclass", "category"),
    )

    # Sizes are conservative; tune to upstream constraints if you know them.
    equipment_no: Mapped[str] = mapped_column(
        "equipmentno", String(64), primary_key=True
    )
    eq_class: Mapped[str | None] = mapped_column("eqclass", String(64), nullable=True)
    category: Mapped[str | None] = mapped_column("category", String(64), nullable=True)
    equipment_desc: Mapped[str | None] = mapped_column(
        "equipmentdesc", Text, nullable=True
    )
    sponsor: Mapped[str | None] = mapped_column("sponsor", String(64), nullable=True)

    parent_asset: Mapped[str | None] = mapped_column(
        "parentasset",
        String(64),
        nullable=True,
    )

    commission_date: Mapped[date | None] = mapped_column(
        "commissiondate", Date, nullable=True
    )
    asset_status_display: Mapped[str | None] = mapped_column(
        "assetstatus_display", String(64), nullable=True
    )

    # --- Converters ----------------------------------------------------------

    @classmethod
    def from_position(cls, position: EAMPosition) -> EAMPositionORM:
        """Create an ORM row from a domain model."""
        # EAMPosition.commission_date is already a date | None in your Pydantic model.
        cd = position.commission_date
        cd_parsed: date | None
        if isinstance(cd, date):
            cd_parsed = cd
        elif isinstance(cd, str) and cd:
            try:
                cd_parsed = date.fromisoformat(cd)
            except ValueError:
                cd_parsed = None
        else:
            cd_parsed = None

        return cls(
            equipment_no=position.equipment_no,
            eq_class=position.eq_class,
            category=position.category,
            equipment_desc=position.equipment_desc,
            sponsor=position.sponsor,
            parent_asset=position.parent_asset,
            commission_date=cd_parsed,
            asset_status_display=position.asset_status_display,
        )

    def to_position(self) -> EAMPosition:
        """Convert this row back to the domain model."""
        try:
            return EAMPosition.model_validate(self, from_attributes=True)  # type: ignore[attr-defined]
        except AttributeError:
            return EAMPosition(
                equipment_no=self.equipment_no,
                eq_class=self.eq_class,
                category=self.category,
                equipment_desc=self.equipment_desc,
                sponsor=self.sponsor,
                parent_asset=self.parent_asset,
                commission_date=(
                    self.commission_date.isoformat() if self.commission_date else None
                ),
                asset_status_display=self.asset_status_display,
            )

    # --- Debug ---------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            "EAMPositionORM("
            f"equipment_no={self.equipment_no!r}, "
            f"parent_asset={self.parent_asset!r}, "
            f"eq_class={self.eq_class!r}, "
            f"category={self.category!r}, "
            f"status={self.asset_status_display!r}"
            ")"
        )
