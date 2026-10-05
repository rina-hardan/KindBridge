-- ===================================================
-- KindBridge - Complete MS SQL Database Schema (v1.1)
-- File: db/schema.sql
-- ===================================================

-- 1. Event Store (Source of Truth - CQRS / Event Sourcing)
IF OBJECT_ID('dbo.event_store', 'U') IS NOT NULL DROP TABLE dbo.event_store;
CREATE TABLE dbo.event_store (
    seq BIGINT IDENTITY(1,1) NOT NULL,
    event_id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    aggregate_id UNIQUEIDENTIFIER NOT NULL,
    aggregate_type NVARCHAR(50) NOT NULL, -- User, HelpRequest, ExemptionLink
    event_type NVARCHAR(80) NOT NULL,
    payload_json NVARCHAR(MAX) NOT NULL,
    version INT NOT NULL,
    correlation_id UNIQUEIDENTIFIER NULL,
    causation_id UNIQUEIDENTIFIER NULL,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CONSTRAINT UQ_event_store_aggregate_version UNIQUE (aggregate_id, version)
);
CREATE UNIQUE INDEX IX_event_store_seq ON dbo.event_store(seq);
CREATE INDEX IX_event_store_aggregate_id ON dbo.event_store(aggregate_id);

-- 2. Users (Core Identity)
IF OBJECT_ID('dbo.users', 'U') IS NOT NULL DROP TABLE dbo.users;
CREATE TABLE dbo.users (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    email NVARCHAR(255) NOT NULL UNIQUE,
    password_hash NVARCHAR(255) NOT NULL,
    full_name NVARCHAR(200) NOT NULL,
    phone NVARCHAR(400) NOT NULL, -- Encrypted app-level
    is_admin BIT NOT NULL DEFAULT 0,
    is_active BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE()
);

-- 3. Requester Profiles (1:1 Extension for Requesters)
IF OBJECT_ID('dbo.requester_profiles', 'U') IS NOT NULL DROP TABLE dbo.requester_profiles;
CREATE TABLE dbo.requester_profiles (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    user_id UNIQUEIDENTIFIER NOT NULL UNIQUE FOREIGN KEY REFERENCES dbo.users(id) ON DELETE CASCADE,
    default_city NVARCHAR(100) NULL,
    default_address NVARCHAR(255) NULL,
    accessibility_notes NVARCHAR(500) NULL,
    emergency_contact_name NVARCHAR(200) NULL,
    emergency_contact_phone NVARCHAR(400) NULL,
    updated_at DATETIME2 NOT NULL DEFAULT GETUTCDATE()
);

-- 4. Volunteer Profiles (1:1 Extension for Volunteers)
IF OBJECT_ID('dbo.volunteer_profiles', 'U') IS NOT NULL DROP TABLE dbo.volunteer_profiles;
CREATE TABLE dbo.volunteer_profiles (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    user_id UNIQUEIDENTIFIER NOT NULL UNIQUE FOREIGN KEY REFERENCES dbo.users(id) ON DELETE CASCADE,
    is_enabled BIT NOT NULL DEFAULT 1,
    primary_city NVARCHAR(100) NOT NULL,
    has_vehicle BIT NOT NULL DEFAULT 0,
    skills_json NVARCHAR(MAX) NOT NULL,
    experience NVARCHAR(MAX) NOT NULL,
    base_frequency NVARCHAR(30) NOT NULL, -- WEEKLY, BIWEEKLY, MONTHLY, ON_DEMAND
    availability_status NVARCHAR(30) NOT NULL DEFAULT 'AVAILABLE', -- AVAILABLE, INACTIVE
    max_active_tasks INT NOT NULL DEFAULT 1,
    max_parallel_tasks INT NOT NULL DEFAULT 2,
    current_active_tasks INT NOT NULL DEFAULT 0,
    current_parallel_tasks INT NOT NULL DEFAULT 0
);
CREATE INDEX IX_volunteer_profiles_primary_city ON dbo.volunteer_profiles(primary_city);

-- 5. Volunteer Unavailability Periods
IF OBJECT_ID('dbo.volunteer_unavailability', 'U') IS NOT NULL DROP TABLE dbo.volunteer_unavailability;
CREATE TABLE dbo.volunteer_unavailability (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    volunteer_id UNIQUEIDENTIFIER NOT NULL FOREIGN KEY REFERENCES dbo.volunteer_profiles(id) ON DELETE CASCADE,
    from_date DATE NOT NULL,
    until_date DATE NOT NULL,
    reason NVARCHAR(200) NULL,
    is_cancelled BIT NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE()
);

-- 6. Request Series (Deferred / Reserved for Recurring Requests)
IF OBJECT_ID('dbo.request_series', 'U') IS NOT NULL DROP TABLE dbo.request_series;
CREATE TABLE dbo.request_series (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    requester_id UNIQUEIDENTIFIER NOT NULL FOREIGN KEY REFERENCES dbo.users(id),
    recurrence NVARCHAR(20) NOT NULL, -- WEEKLY, BIWEEKLY, MONTHLY
    start_date DATE NOT NULL,
    end_date DATE NULL,
    preferred_volunteer_id UNIQUEIDENTIFIER NULL FOREIGN KEY REFERENCES dbo.volunteer_profiles(id),
    is_active BIT NOT NULL DEFAULT 1
);

-- 7. Help Requests
IF OBJECT_ID('dbo.help_requests', 'U') IS NOT NULL DROP TABLE dbo.help_requests;
CREATE TABLE dbo.help_requests (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    requester_id UNIQUEIDENTIFIER NOT NULL FOREIGN KEY REFERENCES dbo.users(id),
    series_id UNIQUEIDENTIFIER NULL FOREIGN KEY REFERENCES dbo.request_series(id),
    city NVARCHAR(100) NOT NULL,
    address NVARCHAR(255) NOT NULL,
    category NVARCHAR(50) NOT NULL,
    resource_type NVARCHAR(30) NOT NULL, -- PHYSICAL_PRESENCE, EQUIPMENT_LOAN, FLEXIBLE_REMOTE
    description NVARCHAR(MAX) NOT NULL,
    urgency NVARCHAR(20) NOT NULL, -- LOW, NORMAL, HIGH, EMERGENCY
    preferred_date DATE NULL,
    preferred_time_from TIME NULL,
    preferred_time_to TIME NULL,
    estimated_duration_min INT NULL,
    required_skills_json NVARCHAR(MAX) NOT NULL,
    requires_vehicle BIT NOT NULL DEFAULT 0,
    concurrency_type NVARCHAR(20) NOT NULL DEFAULT 'UNKNOWN', -- EXCLUSIVE, PARALLEL_OK, UNKNOWN
    status NVARCHAR(30) NOT NULL, -- PENDING_REVIEW, MATCH_PROPOSED, NO_MATCH, ASSIGNED, COMPLETED, CANCELLED
    match_attempt INT NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE()
);
CREATE INDEX IX_help_requests_status ON dbo.help_requests(status);
CREATE INDEX IX_help_requests_city ON dbo.help_requests(city);

-- 8. Task Assignments
IF OBJECT_ID('dbo.task_assignments', 'U') IS NOT NULL DROP TABLE dbo.task_assignments;
CREATE TABLE dbo.task_assignments (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWSEQUENTIALID(),
    request_id UNIQUEIDENTIFIER NOT NULL FOREIGN KEY REFERENCES dbo.help_requests(id) ON DELETE CASCADE,
    volunteer_id UNIQUEIDENTIFIER NOT NULL FOREIGN KEY REFERENCES dbo.volunteer_profiles(id),
    ai_score DECIMAL(5,2) NULL,
    ai_rationale NVARCHAR(500) NULL,
    rank_in_batch INT NULL,
    match_attempt INT NOT NULL,
    approved_by UNIQUEIDENTIFIER NULL FOREIGN KEY REFERENCES dbo.users(id),
    status NVARCHAR(30) NOT NULL, -- PROPOSED, ASSIGNED, DECLINED, COMPLETED, SUPERSEDED
    decline_reason NVARCHAR(500) NULL,
    override_reason NVARCHAR(500) NULL,
    updated_at DATETIME2 NOT NULL DEFAULT GETUTCDATE()
);

-- 9. Exemption Links (Mutual Exclusions)
IF OBJECT_ID('dbo.exemption_links', 'U') IS NOT NULL DROP TABLE dbo.exemption_links;
CREATE TABLE dbo.exemption_links (
    volunteer_id UNIQUEIDENTIFIER NOT NULL FOREIGN KEY REFERENCES dbo.users(id),
    requester_id UNIQUEIDENTIFIER NOT NULL FOREIGN KEY REFERENCES dbo.users(id),
    created_by UNIQUEIDENTIFIER NOT NULL FOREIGN KEY REFERENCES dbo.users(id),
    reason NVARCHAR(300) NOT NULL,
    created_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    PRIMARY KEY (volunteer_id, requester_id)
);

-- 10. Login Attempts (Rate Limiting & Security Audit)
IF OBJECT_ID('dbo.login_attempts', 'U') IS NOT NULL DROP TABLE dbo.login_attempts;
CREATE TABLE dbo.login_attempts (
    id BIGINT IDENTITY(1,1) PRIMARY KEY,
    email NVARCHAR(255) NOT NULL,
    attempted_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    succeeded BIT NOT NULL
);
CREATE INDEX IX_login_attempts_email_attempted_at ON dbo.login_attempts(email, attempted_at);