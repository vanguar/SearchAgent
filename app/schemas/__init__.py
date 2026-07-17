from app.schemas.audit import UserActionAuditSchema
from app.schemas.crm import (
    ApplicationLeadSchema,
    InterviewEventSchema,
    LeadEventSchema,
    ManualNoteSchema,
    ReminderTaskSchema,
)
from app.schemas.documents import CoverLetterTemplateSchema, ResumeSchema, ResumeVersionSchema
from app.schemas.email import EmailMessageSchema, EmailThreadSchema
from app.schemas.profiles import SearchProfileSchema, UserProfileSchema
from app.schemas.search import SearchRunSchema, VacancyScoreSchema
from app.schemas.vacancies import VacancyCanonicalSchema, VacancySourceRecordSchema

__all__ = [
    "ApplicationLeadSchema",
    "CoverLetterTemplateSchema",
    "EmailMessageSchema",
    "EmailThreadSchema",
    "InterviewEventSchema",
    "LeadEventSchema",
    "ManualNoteSchema",
    "ReminderTaskSchema",
    "ResumeSchema",
    "ResumeVersionSchema",
    "SearchProfileSchema",
    "SearchRunSchema",
    "UserActionAuditSchema",
    "UserProfileSchema",
    "VacancyCanonicalSchema",
    "VacancyScoreSchema",
    "VacancySourceRecordSchema",
]
