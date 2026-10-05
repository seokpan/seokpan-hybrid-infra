-- Synthetic local fixture schema derived from seokpan-hybrid-app
-- c12b3d15a4dd2c806fac4326a9eb30ed6e8a81b3 / revisions 20260901_0001 + 20260902_0002.
-- This is not the operational Migration CLI and must never run against a shared DB.
CREATE DATABASE stone_game CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE stone_game;
CREATE TABLE member (
 member_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
 login_id VARCHAR(32) COLLATE utf8mb4_bin NOT NULL,
 nickname VARCHAR(12) COLLATE utf8mb4_bin NOT NULL,
 password_hash VARCHAR(255) NOT NULL,
 rating INTEGER NOT NULL DEFAULT 1000,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
 PRIMARY KEY (member_id), CONSTRAINT uk_member_login_id UNIQUE (login_id),
 CONSTRAINT uk_member_nickname UNIQUE (nickname),
 CONSTRAINT chk_member_rating_nonneg CHECK (rating >= 0),
 CONSTRAINT chk_member_nickname_len CHECK (CHAR_LENGTH(nickname) BETWEEN 2 AND 12)
) ENGINE=InnoDB CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE member_stats (
 member_id BIGINT UNSIGNED NOT NULL,
 wins INTEGER UNSIGNED NOT NULL DEFAULT 0, draws INTEGER UNSIGNED NOT NULL DEFAULT 0,
 losses INTEGER UNSIGNED NOT NULL DEFAULT 0, games_played INTEGER UNSIGNED NOT NULL DEFAULT 0,
 updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
 PRIMARY KEY (member_id), CONSTRAINT fk_member_stats_member FOREIGN KEY (member_id) REFERENCES member(member_id)
) ENGINE=InnoDB CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE game (
 game_id CHAR(36) NOT NULL, room_id VARCHAR(64), voting_time_seconds TINYINT NOT NULL,
 status ENUM('IN_PROGRESS','COMPLETED','SYSTEM_INVALID') NOT NULL DEFAULT 'IN_PROGRESS',
 started_at DATETIME(3) NOT NULL, ended_at DATETIME(3), PRIMARY KEY(game_id),
 CONSTRAINT chk_game_voting_time CHECK (voting_time_seconds IN (5,10,15,30))
) ENGINE=InnoDB CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE game_participant (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, game_id CHAR(36) NOT NULL,
 team ENUM('BLACK','WHITE') NOT NULL, member_id BIGINT UNSIGNED,
 is_guest BOOL NOT NULL, guest_label VARCHAR(10), PRIMARY KEY(id),
 CONSTRAINT chk_participant_guest_label CHECK ((is_guest = FALSE AND guest_label IS NULL) OR (is_guest = TRUE AND guest_label REGEXP '^Guest-[0-9]{4}$')),
 INDEX idx_participant_game (game_id), INDEX fk_participant_member (member_id),
 CONSTRAINT fk_participant_game FOREIGN KEY(game_id) REFERENCES game(game_id),
 CONSTRAINT fk_participant_member FOREIGN KEY(member_id) REFERENCES member(member_id)
) ENGINE=InnoDB CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE move (
 game_id CHAR(36) NOT NULL, turn_no SMALLINT UNSIGNED NOT NULL, move_no SMALLINT UNSIGNED NOT NULL,
 team ENUM('BLACK','WHITE') NOT NULL, pos_x TINYINT UNSIGNED NOT NULL, pos_y TINYINT UNSIGNED NOT NULL,
 final_vote_count SMALLINT UNSIGNED NOT NULL, valid_voter_count SMALLINT UNSIGNED NOT NULL,
 confirmed_at DATETIME(3) NOT NULL, PRIMARY KEY(game_id,turn_no),
 CONSTRAINT uk_move_game_move_no UNIQUE(game_id,move_no),
 CONSTRAINT fk_move_game FOREIGN KEY(game_id) REFERENCES game(game_id),
 CONSTRAINT chk_move_pos_x CHECK(pos_x BETWEEN 0 AND 14), CONSTRAINT chk_move_pos_y CHECK(pos_y BETWEEN 0 AND 14)
) ENGINE=InnoDB CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE game_result (
 game_id CHAR(36) NOT NULL, winner ENUM('BLACK','WHITE','DRAW','NONE') NOT NULL,
 end_reason ENUM('NORMAL_WIN','DRAW','FORFEIT','MUTUAL_FORFEIT','SYSTEM_INVALID') NOT NULL,
 reflected_to_stats BOOL NOT NULL DEFAULT 0, ended_at DATETIME(3) NOT NULL, PRIMARY KEY(game_id),
 CONSTRAINT fk_result_game FOREIGN KEY(game_id) REFERENCES game(game_id)
) ENGINE=InnoDB CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE rating_history (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, member_id BIGINT UNSIGNED NOT NULL, game_id CHAR(36) NOT NULL,
 rating_before INTEGER NOT NULL, rating_after INTEGER NOT NULL, rating_delta INTEGER NOT NULL,
 recorded_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), PRIMARY KEY(id),
 CONSTRAINT uk_rating_member_game UNIQUE(member_id,game_id), INDEX fk_rating_game(game_id),
 CONSTRAINT fk_rating_member FOREIGN KEY(member_id) REFERENCES member(member_id),
 CONSTRAINT fk_rating_game FOREIGN KEY(game_id) REFERENCES game(game_id)
) ENGINE=InnoDB CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
ALTER TABLE game_participant ADD COLUMN participant_id CHAR(36) NULL;
CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL, PRIMARY KEY(version_num));
INSERT INTO alembic_version VALUES ('20260902_0002');
