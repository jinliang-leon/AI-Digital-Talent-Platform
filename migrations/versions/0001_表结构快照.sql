CREATE TABLE permissions (
	code VARCHAR(80) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code)
);

CREATE TABLE processing_tasks (
	task_type VARCHAR(30) NOT NULL, 
	entity_id VARCHAR(36) NOT NULL, 
	revision INTEGER NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	stage VARCHAR(80) NOT NULL, 
	attempts INTEGER NOT NULL, 
	error_code VARCHAR(80), 
	safe_error_message VARCHAR(255), 
	heartbeat_at DATETIME, 
	started_at DATETIME, 
	finished_at DATETIME, 
	id VARCHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_processing_tasks_entity_id ON processing_tasks (entity_id);

CREATE INDEX ix_processing_tasks_state ON processing_tasks (state);

CREATE TABLE roles (
	code VARCHAR(32) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code)
);

CREATE TABLE tags (
	name VARCHAR(60) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE users (
	username VARCHAR(80) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	is_active BOOL NOT NULL, 
	auth_version INTEGER NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);

CREATE TABLE audit_logs (
	actor_id VARCHAR(36), 
	action VARCHAR(80) NOT NULL, 
	entity_type VARCHAR(50) NOT NULL, 
	entity_id VARCHAR(36), 
	outcome VARCHAR(80) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(actor_id) REFERENCES users (id)
);

CREATE TABLE index_outbox (
	entity_type VARCHAR(30) NOT NULL, 
	entity_id VARCHAR(36) NOT NULL, 
	revision INTEGER NOT NULL, 
	operation VARCHAR(24) NOT NULL, 
	task_id VARCHAR(36) NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	attempts INTEGER NOT NULL, 
	next_attempt_at DATETIME NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (entity_type, entity_id, revision, operation), 
	FOREIGN KEY(task_id) REFERENCES processing_tasks (id)
);

CREATE INDEX ix_index_outbox_state ON index_outbox (state);

CREATE TABLE jobs (
	title VARCHAR(150) NOT NULL, 
	raw_jd TEXT NOT NULL, 
	requirements JSON NOT NULL, 
	hard_conditions JSON NOT NULL, 
	hard_confirmed BOOL NOT NULL, 
	revision INTEGER NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	index_status VARCHAR(24) NOT NULL, 
	created_by VARCHAR(36) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by) REFERENCES users (id)
);

CREATE TABLE role_permissions (
	role_id VARCHAR(36) NOT NULL, 
	permission_id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (role_id, permission_id), 
	FOREIGN KEY(role_id) REFERENCES roles (id), 
	FOREIGN KEY(permission_id) REFERENCES permissions (id)
);

CREATE TABLE talents (
	name VARCHAR(120) NOT NULL, 
	summary TEXT NOT NULL, 
	facts JSON NOT NULL, 
	revision INTEGER NOT NULL, 
	active_document_id VARCHAR(36), 
	index_status VARCHAR(24) NOT NULL, 
	deleted_at DATETIME, 
	created_by VARCHAR(36) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by) REFERENCES users (id)
);

CREATE INDEX ix_talents_index_status ON talents (index_status);

CREATE TABLE user_roles (
	user_id VARCHAR(36) NOT NULL, 
	role_id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (user_id, role_id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(role_id) REFERENCES roles (id)
);

CREATE TABLE match_results (
	job_id VARCHAR(36) NOT NULL, 
	job_revision INTEGER NOT NULL, 
	talent_id VARCHAR(36) NOT NULL, 
	talent_revision INTEGER NOT NULL, 
	algorithm_version VARCHAR(60) NOT NULL, 
	embedding_model VARCHAR(100) NOT NULL, 
	score_json JSON NOT NULL, 
	evidence_json JSON NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (job_id, job_revision, talent_id, talent_revision, algorithm_version), 
	FOREIGN KEY(job_id) REFERENCES jobs (id), 
	FOREIGN KEY(talent_id) REFERENCES talents (id)
);

CREATE TABLE resume_documents (
	talent_id VARCHAR(36) NOT NULL, 
	original_name VARCHAR(255) NOT NULL, 
	object_key VARCHAR(255) NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	mime_type VARCHAR(100) NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	revision INTEGER NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	extracted_text TEXT NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(talent_id) REFERENCES talents (id), 
	UNIQUE (object_key), 
	UNIQUE (sha256)
);

CREATE INDEX ix_resume_documents_talent_id ON resume_documents (talent_id);

CREATE TABLE talent_notes (
	talent_id VARCHAR(36) NOT NULL, 
	author_id VARCHAR(36) NOT NULL, 
	content TEXT NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(talent_id) REFERENCES talents (id), 
	FOREIGN KEY(author_id) REFERENCES users (id)
);

CREATE INDEX ix_talent_notes_talent_id ON talent_notes (talent_id);

CREATE TABLE talent_revisions (
	talent_id VARCHAR(36) NOT NULL, 
	revision INTEGER NOT NULL, 
	document_id VARCHAR(36), 
	factual_snapshot JSON NOT NULL, 
	evidence_snapshot JSON NOT NULL, 
	actor_id VARCHAR(36), 
	reason VARCHAR(100) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (talent_id, revision), 
	FOREIGN KEY(talent_id) REFERENCES talents (id), 
	FOREIGN KEY(actor_id) REFERENCES users (id)
);

CREATE TABLE talent_tags (
	talent_id VARCHAR(36) NOT NULL, 
	tag_id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (talent_id, tag_id), 
	FOREIGN KEY(talent_id) REFERENCES talents (id), 
	FOREIGN KEY(tag_id) REFERENCES tags (id)
);

CREATE TABLE source_chunks (
	document_id VARCHAR(36) NOT NULL, 
	document_revision INTEGER NOT NULL, 
	chunk_no INTEGER NOT NULL, 
	text TEXT NOT NULL, 
	page_no INTEGER, 
	paragraph_no INTEGER, 
	char_start INTEGER NOT NULL, 
	char_end INTEGER NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (document_id, document_revision, chunk_no), 
	FOREIGN KEY(document_id) REFERENCES resume_documents (id)
);

CREATE INDEX ix_source_chunks_document_id ON source_chunks (document_id);

CREATE TABLE extraction_evidence (
	talent_id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36) NOT NULL, 
	document_revision INTEGER NOT NULL, 
	talent_revision INTEGER NOT NULL, 
	field_path VARCHAR(100) NOT NULL, 
	chunk_id VARCHAR(36) NOT NULL, 
	quote TEXT NOT NULL, 
	origin VARCHAR(30) NOT NULL, 
	verified_by VARCHAR(36), 
	id VARCHAR(36) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(talent_id) REFERENCES talents (id), 
	FOREIGN KEY(document_id) REFERENCES resume_documents (id), 
	FOREIGN KEY(chunk_id) REFERENCES source_chunks (id), 
	FOREIGN KEY(verified_by) REFERENCES users (id)
);

CREATE INDEX ix_extraction_evidence_talent_id ON extraction_evidence (talent_id);
