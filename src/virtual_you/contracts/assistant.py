"""Member 4 inputs. These never grant approval or authorization to send."""

from typing import Optional

from pydantic import Field

from virtual_you.contracts.reporting import Contract, Destination, DraftRequest, RevisionRequest


class QuestionRequest(DraftRequest):
    request_id: str = Field(min_length=1, max_length=160, pattern=r"^[\w.:-]+$")
    question: str = Field(min_length=1, max_length=1000)


class ResolveEscalation(Contract):
    note: str = Field(min_length=1, max_length=1000)


class VoiceEdit(RevisionRequest):
    transcript: str = Field(min_length=1, max_length=12000)


class VoiceConfirm(RevisionRequest):
    recipient_id: str = Field(min_length=1, max_length=80, pattern=r"^[\w.-]+$")
    project: str = Field(min_length=1, max_length=80)
    destination: Destination
    transcript: Optional[str] = Field(default=None, min_length=1, max_length=12000)
