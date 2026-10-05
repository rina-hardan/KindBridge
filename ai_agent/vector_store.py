import os

class VolunteerVectorStore:
    def __init__(self, persist_directory="./chroma_db"):
        # Local storage mock for volunteer profiles without heavy C-dependencies
        self.volunteers = {}

    def add_volunteer(self, volunteer_id: str, experience_text: str, skills: list, city: str):
        """
        Add or update a volunteer profile in the local memory store
        """
        self.volunteers[volunteer_id] = {
            "id": volunteer_id,
            "experience_text": experience_text.lower(),
            "skills": [s.lower() for s in skills],
            "city": city
        }

    def search_top_volunteers(self, request_description: str, top_k: int = 3):
        """
        Simulate semantic matching by scoring volunteers based on skill and text overlap
        """
        query = request_description.lower()
        scored_results = []

        for vol_id, profile in self.volunteers.items():
            # Calculate a basic relevance score based on matching keywords
            score = 0
            for skill in profile["skills"]:
                if skill in query:
                    score += 2.0
            
            # Check words in experience text
            for word in query.split():
                if len(word) > 3 and word in profile["experience_text"]:
                    score += 0.5

            scored_results.append({
                "id": vol_id,
                "score": score
            })

        # Sort by highest score descending
        scored_results.sort(key=lambda x: x["score"], reverse=True)
        
        top_matches = scored_results[:top_k]
        
        # Return format compatible with the matching engine expectation
        return {
            "ids": [[m["id"] for m in top_matches]],
            "distances": [[1.0 / (m["score"] + 1.0) for m in top_matches]]
        }
        