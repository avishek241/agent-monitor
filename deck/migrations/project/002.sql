-- Agent Deck project store, schema 2 (W0 integration): the workspace an attempt ran in is kept on the attempt.
ALTER TABLE attempts ADD COLUMN workspace TEXT;
ALTER TABLE attempts ADD COLUMN branch TEXT;
