# ChorusRank: Gold Set Labeling Rubric

To ensure rigorous, non-circular evaluation for CSE 881, we create a hand-labeled benchmark of 200–300 accounts.

---

## 1. Class Definitions

### Class 1: `INAUTHENTIC_COORDINATED`
An account exhibiting clear behavioral evidence of automated, synchronized, or coordinated campaigning.
* **Positive Indicators:**
  - Posting identical or near-verbatim text templates across different accounts with minimal time lag.
  - Accounts with random alphanumeric handles or automated naming patterns exclusively pushing specific redirect links (`t.me/`, airdrop claims, phishing recovery).
  - Repost/amplification-only activity targeted at promoting a single external entity or scam campaign without organic human dialogue.
* **Strict Non-Circularity Rule:**  
  Do **NOT** label an account as inauthentic solely because MinHash or Louvain clustered it. Ground labels in **external post evidence**, account history, and human inspection.

### Class 2: `ORGANIC_BENIGN`
An ordinary social media user.
* **Positive Indicators:**
  - Conversational, context-dependent text (replies to friends, personal opinions, jokes, questions).
  - Varied interests, distinct vocabulary, and organic posting intervals.
  - Active profile customization with genuine follower/following interactions.

### Class 3: `UNCERTAIN`
Accounts where human annotators cannot reach consensus or evidence is ambiguous (e.g., automated news RSS bots, promotional artist accounts, single-post dormant accounts). Excluded from primary binary F1 calculation or evaluated as a separate category.

---

## 2. Independent Annotation Protocol

1. Two annotators label the exported `gold_set_sample.csv` independently without viewing the other's tags.
2. Compute **Cohen's Kappa ($\kappa$)** or percentage agreement across the two annotators.
3. Disagreements are reviewed and either resolved by consensus or moved to `UNCERTAIN`.
4. Validate the resulting gold set against longitudinal takedowns: record what percentage of `INAUTHENTIC_COORDINATED` accounts were later taken down by Bluesky Trust & Safety.
