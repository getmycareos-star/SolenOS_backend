"""
Simple test for Person Responsibility models without full app initialization.
"""

import sys
import os

# Add the backend directory to the path so we can import app modules
sys.path.insert(0, 'C:\\solenos\\backend')

try:
    from app.models.person_relationship import PersonRelationship
    from app.models.person_role import PersonRole
    from app.models.person_responsibility import PersonResponsibility
    from app.models.person_availability import PersonAvailability
    from app.models.information_contribution import InformationContribution
    print("All person responsibility models imported successfully!")
    
    # Print model info to verify they're defined correctly
    print(f"PersonRelationship: {PersonRelationship}")
    print(f"PersonRole: {PersonRole}")
    print(f"PersonResponsibility: {PersonResponsibility}")
    print(f"PersonAvailability: {PersonAvailability}")
    print(f"InformationContribution: {InformationContribution}")
    
except Exception as e:
    print(f"Error importing models: {e}")
    import traceback
    traceback.print_exc()