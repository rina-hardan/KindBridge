from ai_agent.vector_store import VolunteerVectorStore
from ai_agent.matching_engine import MatchingEngine
from datetime import date

def run_test():
    # Initialize vector store with a test directory
    vector_store = VolunteerVectorStore(persist_directory="./test_chroma_db")
    
    # Add mock volunteers to the vector store
    vector_store.add_volunteer(
        volunteer_id="vol-1",
        experience_text="Experienced in tutoring mathematics, computer science, and helping students with calculus and algebra.",
        skills=["math", "tutoring", "algebra"],
        city="Jerusalem"
    )
    vector_store.add_volunteer(
        volunteer_id="vol-2",
        experience_text="Professional driver with a large car, ready to deliver food, packages, and equipment anywhere.",
        skills=["driving", "delivery"],
        city="Jerusalem"
    )
    vector_store.add_volunteer(
        volunteer_id="vol-3",
        experience_text="Medical student, experienced in first aid, caregiving, and biology tutoring.",
        skills=["first_aid", "biology"],
        city="Tel Aviv" # Different city - should fail hard filter for physical presence in Jerusalem
    )

    # Initialize matching engine
    engine = MatchingEngine(vector_store)

    # Mock help request
    help_request = {
        "id": "req-101",
        "requester_id": "user-999",
        "city": "Jerusalem",
        "resource_type": "PHYSICAL_PRESENCE",
        "description": "Looking for someone to give me private lessons and tutoring in advanced mathematics.",
        "requires_vehicle": False,
        "concurrency_type": "EXCLUSIVE",
        "preferred_date": date.today()
    }

    # Mock full volunteer records (matching the database schema projections)
    all_volunteers = [
        {
            "id": "vol-1", 
            "user_id": "u-1", 
            "is_enabled": True, 
            "is_active": True, 
            "availability_status": "AVAILABLE", 
            "primary_city": "Jerusalem", 
            "has_vehicle": True, 
            "current_active_tasks": 0, 
            "max_active_tasks": 1
        },
        {
            "id": "vol-2", 
            "user_id": "u-2", 
            "is_enabled": True, 
            "is_active": True, 
            "availability_status": "AVAILABLE", 
            "primary_city": "Jerusalem", 
            "has_vehicle": True, 
            "current_active_tasks": 0, 
            "max_active_tasks": 1
        },
        {
            "id": "vol-3", 
            "user_id": "u-3", 
            "is_enabled": True, 
            "is_active": True, 
            "availability_status": "AVAILABLE", 
            "primary_city": "Tel Aviv", # Will fail city mismatch filter
            "has_vehicle": False, 
            "current_active_tasks": 0, 
            "max_active_tasks": 1
        }
    ]
    
    exemptions = []
    declined_volunteers = []
    active_unavailabilities = []

    # Run the matching process
    print("Running matching algorithm...")
    result = engine.process_match_request(
        request=help_request,
        all_volunteers=all_volunteers,
        exemptions=exemptions,
        declined_volunteers=declined_volunteers,
    active_unavailabilities=active_unavailabilities,
        top_k=3
    )

    print("\n--- Matching Results ---")
    print(result)

if __name__ == "__main__":
    run_test()
    