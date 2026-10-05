from ai_agent.vector_store import VolunteerVectorStore
from ai_agent.hard_filter import HardFilter

class MatchingEngine:
    def __init__(self, vector_store: VolunteerVectorStore):
        self.vector_store = vector_store

    def process_match_request(self, request: dict, all_volunteers: list, exemptions: list, declined_volunteers: list, active_unavailabilities: list, top_k: int = 3):
        """
        Executes the two-stage matching process:
        Stage 1: Hard filtering (deterministic constraints).
        Stage 2: Semantic search / RAG (scoring filtered candidates).
        Returns the top-K matching volunteers.
        """
        passed_volunteers = []
        rejection_summary = {}

        # Stage 1: Apply Hard Filters
        for volunteer in all_volunteers:
            is_valid, reason = HardFilter.evaluate_volunteer(
                volunteer=volunteer,
                request=request,
                exemptions=exemptions,
                declined_volunteers=declined_volunteers,
                active_unavailabilities=active_unavailabilities
            )
            
            if is_valid:
                passed_volunteers.append(volunteer)
            else:
                # Count rejections per reason (useful for NoMatchFound payload)
                rejection_summary[reason] = rejection_summary.get(reason, 0) + 1

        # If no volunteers passed the hard filter
        if not passed_volunteers:
            return {
                "status": "NO_MATCH",
                "rejection_summary": rejection_summary,
                "candidates": []
            }

        # Stage 2: Semantic Search / RAG on filtered candidates
        # Ensure that only volunteers who passed the hard filter are queried or scored
        request_description = request.get("description", "") + " " + request.get("accessibility_notes", "")
        
        # Query ChromaDB for semantic matches
        # Note: In production, we filter the vector search results to include only IDs of 'passed_volunteers'
        passed_ids = [v["id"] for v in passed_volunteers]
        
        vector_results = self.vector_store.search_top_volunteers(
            request_description=request_description,
            top_k=top_k
        )

        # Filter vector results to match only the eligible hard-passed candidates
        final_candidates = []
        if vector_results and "ids" in vector_results and vector_results["ids"]:
            found_ids = vector_results["ids"][0]
            found_distances = vector_results["distances"][0] if "distances" in vector_results else [0] * len(found_ids)
            
            for vol_id, distance in zip(found_ids, found_distances):
                if vol_id in passed_ids:
                    # Find original volunteer details
                    volunteer_obj = next((v for v in passed_volunteers if v["id"] == vol_id), None)
                    if volunteer_obj:
                        final_candidates.append({
                            "volunteer_id": vol_id,
                            "volunteer_name": volunteer_obj.get("full_name"),
                            "distance_score": distance, # Lower distance means higher semantic similarity in ChromaDB
                            "rationale": f"Matched based on semantic profile similarity in city {volunteer_obj.get('primary_city')}."
                        })

                # Stop once we reach top_k valid candidates
                if len(final_candidates) >= top_k:
                    break

        if not final_candidates:
            return {
                "status": "NO_MATCH",
                "rejection_summary": {"SEMANTIC_FILTER_NO_OVERLAP": len(passed_volunteers)},
                "candidates": []
            }

        return {
            "status": "MATCH_PROPOSED",
            "candidates": final_candidates
        }
        