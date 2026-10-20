import graphene
from fastapi import Depends, FastAPI
from graphene_fastapi import GraphQLRouter
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.identity import Person
from app.models.flexible_document import FlexibleDocument
import json


# Define GraphQL types that map to our models
class PersonType(graphene.ObjectType):
    id = graphene.ID()
    person_type = graphene.String()
    name = graphene.String()
    date_of_birth = graphene.DateTime()
    legal_name = graphene.String()
    preferred_name = graphene.String()
    phone = graphene.String()
    address = graphene.String()
    emergency_contact = graphene.String()
    primary_condition = graphene.String()
    conditions = graphene.String()
    care_level = graphene.String()
    is_active = graphene.Boolean()
    is_deceased = graphene.Boolean()
    deceased_at = graphene.DateTime()
    created_by_account_id = graphene.ID()
    created_at = graphene.DateTime()
    updated_at = graphene.DateTime()
    deleted_at = graphene.DateTime()
    
    class Meta:
        # Exclude SQLAlchemy internal attributes
        exclude = ('_sa_instance_state',)


class FlexibleDocumentType(graphene.ObjectType):
    id = graphene.ID()
    person_id = graphene.ID()
    caregiver_id = graphene.ID()
    document_type = graphene.String()
    subtype = graphene.String()
    title = graphene.String()
    description = graphene.String()
    content_text = graphene.String()
    content_json = graphene.String()
    content_blob = graphene.String()
    attributes = graphene.String()
    tags = graphene.String()
    processing_status = graphene.String()
    processing_error = graphene.String()
    source_type = graphene.String()
    source_id = graphene.ID()
    file_name = graphene.String()
    file_size = graphene.Int()
    content_hash = graphene.String()
    is_public = graphene.Boolean()
    is_restricted = graphene.Boolean()
    access_level = graphene.String()
    created_at = graphene.DateTime()
    updated_at = graphene.DateTime()
    created_by = graphene.ID()
    version = graphene.Int()
    parent_id = graphene.ID()
    
    class Meta:
        # Exclude SQLAlchemy internal attributes
        exclude = ('_sa_instance_state',)


class Query(graphene.ObjectType):
    # Hello world for testing
    hello = graphene.String(name=graphene.String(default_value="Stranger"))
    
    # Person queries
    person = graphene.Field(PersonType, id=graphene.ID(required=True))
    persons = graphene.List(
        PersonType, 
        limit=graphene.Int(), 
        offset=graphene.Int(),
        is_active=graphene.Boolean()
    )
    
    # Flexible document queries
    flexible_document = graphene.Field(FlexibleDocumentType, id=graphene.ID(required=True))
    flexible_documents = graphene.List(
        FlexibleDocumentType,
        person_id=graphene.ID(),
        document_type=graphene.String(),
        limit=graphene.Int(),
        offset=graphene.Int()
    )
    
    def resolve_hello(self, info, name):
        return f'Hello {name}!'
    
    def resolve_person(self, info, id):
        # Get database session from context
        db: Session = info.context['db']
        return db.query(Person).filter(Person.id == id).first()
    
    def resolve_persons(self, info, limit=None, offset=None, is_active=None):
        # Get database session from context
        db: Session = info.context['db']
        query = db.query(Person)
        
        if is_active is not None:
            query = query.filter(Person.is_active == is_active)
            
        if offset is not None:
            query = query.offset(offset)
            
        if limit is not None:
            query = query.limit(limit)
            
        return query.all()
    
    def resolve_flexible_document(self, info, id):
        # Get database session from context
        db: Session = info.context['db']
        return db.query(FlexibleDocument).filter(FlexibleDocument.id == id).first()
    
    def resolve_flexible_documents(self, info, person_id=None, document_type=None, limit=None, offset=None):
        # Get database session from context
        db: Session = info.context['db']
        query = db.query(FlexibleDocument)
        
        if person_id is not None:
            query = query.filter(FlexibleDocument.person_id == person_id)
            
        if document_type is not None:
            query = query.filter(FlexibleDocument.document_type == document_type)
            
        if offset is not None:
            query = query.offset(offset)
            
        if limit is not None:
            query = query.limit(limit)
            
        return query.all()


class Mutation(graphene.ObjectType):
    # Simple mutation for testing
    set_message = graphene.String(message=graphene.String(required=True))
    
    # Person mutations
    create_person = graphene.Field(
        PersonType,
        name=graphene.String(required=True),
        person_type=graphene.String(),
        date_of_birth=graphene.DateTime(),
        legal_name=graphene.String(),
        preferred_name=graphene.String(),
        phone=graphene.String(),
        address=graphene.String(),
        emergency_contact=graphene.String(),
        primary_condition=graphene.String(),
        conditions=graphene.String(),
        care_level=graphene.String(),
        is_active=graphene.Boolean(),
        is_deceased=graphene.Boolean(),
        deceased_at=graphene.DateTime()
    )
    
    update_person = graphene.Field(
        PersonType,
        id=graphene.ID(required=True),
        name=graphene.String(),
        person_type=graphene.String(),
        date_of_birth=graphene.DateTime(),
        legal_name=graphene.String(),
        preferred_name=graphene.String(),
        phone=graphene.String(),
        address=graphene.String(),
        emergency_contact=graphene.String(),
        primary_condition=graphene.String(),
        conditions=graphene.String(),
        care_level=graphene.String(),
        is_active=graphene.Boolean(),
        is_deceased=graphene.Boolean(),
        deceased_at=graphene.DateTime()
    )
    
    # Flexible document mutations
    create_flexible_document = graphene.Field(
        FlexibleDocumentType,
        person_id=graphene.ID(),
        caregiver_id=graphene.ID(),
        document_type=graphene.String(required=True),
        subtype=graphene.String(),
        title=graphene.String(),
        description=graphene.String(),
        content_text=graphene.String(),
        content_json=graphene.String(),
        content_blob=graphene.String(),
        attributes=graphene.String(),
        tags=graphene.String(),
        processing_status=graphene.String(),
        processing_error=graphene.String(),
        source_type=graphene.String(),
        source_id=graphene.ID(),
        file_name=graphene.String(),
        file_size=graphene.Int(),
        content_hash=graphene.String(),
        is_public=graphene.Boolean(),
        is_restricted=graphene.Boolean(),
        access_level=graphene.String(),
        created_by=graphene.ID()
    )
    
    update_flexible_document = graphene.Field(
        FlexibleDocumentType,
        id=graphene.ID(required=True),
        person_id=graphene.ID(),
        caregiver_id=graphene.ID(),
        document_type=graphene.String(),
        subtype=graphene.String(),
        title=graphene.String(),
        description=graphene.String(),
        content_text=graphene.String(),
        content_json=graphene.String(),
        content_blob=graphene.String(),
        attributes=graphene.String(),
        tags=graphene.String(),
        processing_status=graphene.String(),
        processing_error=graphene.String(),
        source_type=graphene.String(),
        source_id=graphene.ID(),
        file_name=graphene.String(),
        file_size=graphene.Int(),
        content_hash=graphene.String(),
        is_public=graphene.Boolean(),
        is_restricted=graphene.Boolean(),
        access_level=graphene.String(),
        created_by=graphene.ID()
    )
    
    def resolve_set_message(self, info, message):
        return f'Message set: {message}'
    
    def resolve_create_person(self, info, **kwargs):
        # Get database session from context
        db: Session = info.context['db']
        
        # Create new person instance
        person = Person(**kwargs)
        db.add(person)
        db.commit()
        db.refresh(person)
        return person
    
    def resolve_update_person(self, info, id, **kwargs):
        # Get database session from context
        db: Session = info.context['db']
        
        # Get existing person
        person = db.query(Person).filter(Person.id == id).first()
        if not person:
            return None
            
        # Update fields that were provided
        for key, value in kwargs.items():
            if value is not None and hasattr(person, key):
                setattr(person, key, value)
                
        db.commit()
        db.refresh(person)
        return person
        
    def resolve_create_flexible_document(self, info, **kwargs):
        # Get database session from context
        db: Session = info.context['db']
        
        # Create new flexible document instance
        flexible_doc = FlexibleDocument(**kwargs)
        db.add(flexible_doc)
        db.commit()
        db.refresh(flexible_doc)
        return flexible_doc
    
    def resolve_update_flexible_document(self, info, id, **kwargs):
        # Get database session from context
        db: Session = info.context['db']
        
        # Get existing flexible document
        flexible_doc = db.query(FlexibleDocument).filter(FlexibleDocument.id == id).first()
        if not flexible_doc:
            return None
            
        # Update fields that were provided
        for key, value in kwargs.items():
            if value is not None and hasattr(flexible_doc, key):
                setattr(flexible_doc, key, value)
                
        db.commit()
        db.refresh(flexible_doc)
        return flexible_doc


# Create schema
schema = graphene.Schema(query=Query, mutation=Mutation)

# Custom GraphQL router that adds database session to context
def get_context(
    db: Session = Depends(get_db),
    # request: Request = None,  # Optional: if you need request info
):
    return {"db": db}

graphql_app = GraphQLRouter(
    schema,
    context_getter=get_context
)


def init_plugin(app: FastAPI) -> None:
    """Initialize the GraphQL plugin."""
    app.include_router(graphql_app, prefix="/graphql")
