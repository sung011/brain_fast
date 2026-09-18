from sqlalchemy.orm import Session

from models.reviewNodeModel import ReviewNodeModel


def create(
    db: Session,
    *,
    rn_u_idx: int | None,
    rn_s_idx: int | None,
    rn_disease: str | None,
    rn_image: str | None,
    rn_solution: str | None,
    state: str = "N",
) -> ReviewNodeModel:
    row = ReviewNodeModel(
        del_yn="N",
        rn_u_idx=rn_u_idx,
        rn_s_idx=rn_s_idx,
        rn_disease=rn_disease,
        rn_image=rn_image,
        rn_solution=rn_solution,
        state=state,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
