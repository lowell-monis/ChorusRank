"""Test script to verify content-similarity-verified deduplication, edit handling, and collision protection."""

from chorusrank.storage import get_storage
from chorusrank.storage.schema import RawEventRecord

storage = get_storage()
t_id = "test_sim_collision_did_rkey123"

# Clean up before testing
storage.events_col.delete_many({"event_id": {"$regex": "^test_sim_collision"}})

# 1. Insert Original Post
p_orig = RawEventRecord(
    event_id=t_id,
    did="test_did",
    rkey="rkey123",
    collection="app.bsky.feed.post",
    text="Exclusive presale whitelist is live! Connect your wallet now at crypto-airdrop.io to claim free tokens.",
    clean_text="exclusive presale whitelist is live connect your wallet now at crypto airdrop io to claim free tokens",
    created_at="2026-10-01T00:00:00Z",
    collected_at="2026-10-01T00:00:00Z",
    query_tag="presale",
    like_count=10,
)
storage.save_raw_events([p_orig])

doc1 = storage.events_col.find_one({"event_id": t_id})
assert doc1["text"] == p_orig.text
assert doc1["like_count"] == 10
assert doc1["matched_queries"] == ["presale"]
print("Test 1 (Initial Insert): PASSED")

# 2. Re-encounter with an EDITED version of the post (updated link & typo fix)
p_edited = RawEventRecord(
    event_id=t_id,
    did="test_did",
    rkey="rkey123",
    collection="app.bsky.feed.post",
    text="Exclusive presale whitelist is live! Connect your wallet now at OFFICIAL-site.io to claim free tokens.",
    clean_text="exclusive presale whitelist is live connect your wallet now at official site io to claim free tokens",
    created_at="2026-10-01T00:00:00Z",
    collected_at="2026-10-02T00:00:00Z",
    query_tag="airdrop",
    like_count=25,
)
storage.save_raw_events([p_edited])

doc2 = storage.events_col.find_one({"event_id": t_id})
assert doc2["text"] == p_edited.text, f"Edited text not updated: {doc2['text']}"
assert doc2.get("is_edited") is True, f"is_edited not True: {doc2.get('is_edited')}"
assert doc2.get("previous_text") == p_orig.text, f"previous_text mismatch: {doc2.get('previous_text')}"
assert doc2["like_count"] == 25, f"like_count not updated: {doc2['like_count']}"
assert set(doc2["matched_queries"]) == {"presale", "airdrop"}, f"matched_queries mismatch: {doc2['matched_queries']}"
print("Test 2 (Edited Post Text & Metrics Updated): PASSED")

# 3. Collision Edge Case: Completely different post matching the same ID
p_collided = RawEventRecord(
    event_id=t_id,
    did="test_did",
    rkey="rkey123",
    collection="app.bsky.feed.post",
    text="Just had a delicious pepperoni pizza in downtown Detroit with family!",
    clean_text="just had a delicious pepperoni pizza in downtown detroit with family",
    created_at="2026-10-03T00:00:00Z",
    collected_at="2026-10-03T00:00:00Z",
    query_tag="pizza",
    like_count=999,
)
storage.save_raw_events([p_collided])

# Verify original edited post is completely UNTOUCHED
doc3 = storage.events_col.find_one({"event_id": t_id})
assert doc3["text"] == p_edited.text, f"Original text was overwritten: {doc3['text']}"
assert doc3["like_count"] == 25, f"Original metrics were corrupted: {doc3['like_count']}"
assert "pizza" not in doc3["matched_queries"], f"Wrong query added to original: {doc3['matched_queries']}"

# Verify colliding post was saved under its own disambiguated collision ID
collision_doc = storage.events_col.find_one({"event_id": {"$regex": f"^{t_id}_collision_"}})
assert collision_doc is not None, "Collided post was not saved with collision ID!"
assert collision_doc["text"] == p_collided.text
assert collision_doc["like_count"] == 999
print("Test 3 (Collision Safeguard & Disambiguation): PASSED")

# Clean up
storage.events_col.delete_many({"event_id": {"$regex": "^test_sim_collision"}})
print("\nALL 3 DEDUPLICATION SIMILARITY & EDIT SCENARIOS FULLY PASSED!")
