CREATE TABLE run (
    file_path text PRIMARY KEY,
    status text NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW','PROCESSED','ERROR')),
    error_message text,
    hades_ancients_version text,
    has_foreign_content boolean,
    build_id text,
    player_id text,
    character text,
    win boolean,
    num_players integer,
    build_type text,
    ascension integer,
    total_playtime bigint,
    total_win_rate double precision,
    num_reloads integer,
    run_playtime integer,
    floor_reached integer,
    killed_by_encounter text
);

CREATE TABLE ancient_choice (
    run_file_path text NOT NULL REFERENCES run(file_path) ON DELETE CASCADE,
    choice_index integer NOT NULL,
    ancient_relic_id text NOT NULL,
    picked boolean NOT NULL,
    PRIMARY KEY (run_file_path, choice_index, ancient_relic_id)
);

CREATE INDEX idx_run_filters ON run (status, character, ascension, num_players);
CREATE INDEX idx_ancient_choice_id ON ancient_choice (ancient_relic_id, picked);
