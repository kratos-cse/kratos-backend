import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from app.models.enums import (
    CapacityType,
    EventCategory,
    EventSlot,
    EventStatus,
    MemberRegistrationMode,
    RegistrationMode,
)
from app.schemas.event import EventDetail, EventListItem

DEMO_EVENTS_DATA = [
    {
        "id": uuid.UUID("10000000-0000-0000-0000-000000000001"),
        "name": "Code Genesis 2026",
        "tagline": "Speed, Logic & Algorithmic Supremacy",
        "short_desc": "Intense individual competitive programming round spanning standard algorithms and tricky edge cases.",
        "long_desc": "Code Genesis is KRATOS' flagship solo competitive programming contest. Tackle algorithmic challenges ranging from dynamic programming and graphs to combinatorial math in a timed ICPC-style arena.",
        "category": EventCategory.TECHNICAL,
        "coordinator": "Aarav Sharma",
        "coord_contact": "+91 98765 43210",
        "fee": Decimal("150.00"),
        "venue": "Computer Center Lab 3",
        "capacity": 120,
        "whatsapp_group_available": True,
        "starts_at": datetime(2026, 10, 15, 9, 30, tzinfo=timezone.utc),
        "ends_at": datetime(2026, 10, 15, 12, 30, tzinfo=timezone.utc),
        "slot": EventSlot.MORNING,
        "status": EventStatus.OPEN,
        "registration_open": True,
        "spots_remaining": 78,
        "team_min_size": 1,
        "team_max_size": 1,
        "required_member_count": 1,
        "substitute_count": 0,
        "allow_individual": True,
        "registration_mode": RegistrationMode.INDIVIDUAL_ONLY,
        "capacity_type": CapacityType.PARTICIPANTS,
        "member_registration_mode": MemberRegistrationMode.SELF_ENTRY,
        "allow_team_invite_flow": False,
        "requires_qr_checkin": True,
        "custom_fields": {"preferred_language": "C++/Python/Java"},
    },
    {
        "id": uuid.UUID("10000000-0000-0000-0000-000000000002"),
        "name": "KRATOS Flagship Hackathon",
        "tagline": "24 Hours of Pure Innovation",
        "short_desc": "Build transformative AI, Web3, and Open Innovation solutions in an exhilarating 24-hour overnight sprint.",
        "long_desc": "Gather your team and build working software prototypes that solve real-world problems in HealthTech, FinTech, EdTech, and Sustainability. Mentors, high-speed Wi-Fi, midnight snacks, and exciting sponsor tracks await!",
        "category": EventCategory.TECHNICAL,
        "coordinator": "Sneha Patel",
        "coord_contact": "+91 98765 43211",
        "fee": Decimal("600.00"),
        "venue": "Main Innovation Hub & Seminar Hall",
        "capacity": 60,
        "whatsapp_group_available": True,
        "starts_at": datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc),
        "ends_at": datetime(2026, 10, 17, 14, 0, tzinfo=timezone.utc),
        "slot": EventSlot.MULTI_DAY,
        "status": EventStatus.OPEN,
        "registration_open": True,
        "spots_remaining": 22,
        "team_min_size": 2,
        "team_max_size": 4,
        "required_member_count": 2,
        "substitute_count": 0,
        "allow_individual": False,
        "registration_mode": RegistrationMode.TEAM_ONLY,
        "capacity_type": CapacityType.TEAMS,
        "member_registration_mode": MemberRegistrationMode.SELF_ENTRY,
        "allow_team_invite_flow": True,
        "requires_qr_checkin": True,
        "custom_fields": {"github_profile": "GitHub URL", "track": "AI / Web3 / Open Innovation"},
    },
    {
        "id": uuid.UUID("10000000-0000-0000-0000-000000000003"),
        "name": "RoboWars: Heavy Metal",
        "tagline": "Sparks, Armor & Pure Mechanical Carnage",
        "short_desc": "Custom combat bots clash in an enclosed battle arena for supremacy.",
        "long_desc": "Design and build 15kg or 30kg combat robots equipped with drum spinners, flippers, or wedges. Compete in 3-minute deathmatches inside a bulletproof polycarbonate arena.",
        "category": EventCategory.PLAYGROUND,
        "coordinator": "Vikram Adve",
        "coord_contact": "+91 98765 43212",
        "fee": Decimal("750.00"),
        "venue": "Mechanical Quadrangle Arena",
        "capacity": 32,
        "whatsapp_group_available": True,
        "starts_at": datetime(2026, 10, 15, 11, 0, tzinfo=timezone.utc),
        "ends_at": datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc),
        "slot": EventSlot.FULL_DAY,
        "status": EventStatus.OPEN,
        "registration_open": True,
        "spots_remaining": 14,
        "team_min_size": 2,
        "team_max_size": 5,
        "required_member_count": 2,
        "substitute_count": 1,
        "allow_individual": False,
        "registration_mode": RegistrationMode.TEAM_ONLY,
        "capacity_type": CapacityType.TEAMS,
        "member_registration_mode": MemberRegistrationMode.SELF_ENTRY,
        "allow_team_invite_flow": True,
        "requires_qr_checkin": True,
        "custom_fields": {"bot_weight_kg": "15kg / 30kg", "weapon_type": "Spinner / Wedge / Flipper"},
    },
    {
        "id": uuid.UUID("10000000-0000-0000-0000-000000000004"),
        "name": "Valorant Campus Cup",
        "tagline": "Plant the Spike, Claim the Crown",
        "short_desc": "5v5 competitive tactical FPS tournament streamed live on campus displays.",
        "long_desc": "5v5 Single Elimination Bracket on LAN. Standard tournament map pool, competitive mode rules. Live casting and grand finals on the main auditorium stage.",
        "category": EventCategory.PLAYGROUND,
        "coordinator": "Rohan Deshmukh",
        "coord_contact": "+91 98765 43213",
        "fee": Decimal("500.00"),
        "venue": "Esports Arena (Room 402)",
        "capacity": 32,
        "whatsapp_group_available": True,
        "starts_at": datetime(2026, 10, 16, 10, 0, tzinfo=timezone.utc),
        "ends_at": datetime(2026, 10, 16, 19, 0, tzinfo=timezone.utc),
        "slot": EventSlot.FULL_DAY,
        "status": EventStatus.OPEN,
        "registration_open": True,
        "spots_remaining": 11,
        "team_min_size": 5,
        "team_max_size": 6,
        "required_member_count": 5,
        "substitute_count": 1,
        "allow_individual": False,
        "registration_mode": RegistrationMode.TEAM_ONLY,
        "capacity_type": CapacityType.TEAMS,
        "member_registration_mode": MemberRegistrationMode.SELF_ENTRY,
        "allow_team_invite_flow": True,
        "requires_qr_checkin": True,
        "custom_fields": {"riot_id": "Riot ID#Tag"},
    },
    {
        "id": uuid.UUID("10000000-0000-0000-0000-000000000005"),
        "name": "IdeaPitch Shark Tank",
        "tagline": "Turn Ideas into Funded Ventures",
        "short_desc": "Pitch your groundbreaking startup ideas to active angel investors and industry leaders.",
        "long_desc": "Present a 5-minute deck followed by a 7-minute grilling session with real venture capitalists, startup founders, and angel syndicates. Opportunity for incubation grants and mentorship.",
        "category": EventCategory.SPARK,
        "coordinator": "Ananya Roy",
        "coord_contact": "+91 98765 43214",
        "fee": Decimal("250.00"),
        "venue": "Executive Management Hall",
        "capacity": 40,
        "whatsapp_group_available": True,
        "starts_at": datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc),
        "ends_at": datetime(2026, 10, 15, 17, 30, tzinfo=timezone.utc),
        "slot": EventSlot.AFTERNOON,
        "status": EventStatus.OPEN,
        "registration_open": True,
        "spots_remaining": 19,
        "team_min_size": 1,
        "team_max_size": 3,
        "required_member_count": 1,
        "substitute_count": 0,
        "allow_individual": True,
        "registration_mode": RegistrationMode.TEAM_OR_INDIVIDUAL,
        "capacity_type": CapacityType.TEAMS,
        "member_registration_mode": MemberRegistrationMode.SELF_ENTRY,
        "allow_team_invite_flow": True,
        "requires_qr_checkin": True,
        "custom_fields": {"startup_name": "Startup/Product Name", "industry": "Sector"},
    },
    {
        "id": uuid.UUID("10000000-0000-0000-0000-000000000006"),
        "name": "CyberSiege: Capture The Flag",
        "tagline": "Deconstruct, Exploit & Defend",
        "short_desc": "Jeopardy-style CTF covering Web Security, Reverse Engineering, Cryptography, and Forensics.",
        "long_desc": "A 12-hour cyber security competition designed for beginners and seasoned hackers alike. Crack crypto ciphers, analyze pcap dumps, and exploit vulnerable binaries.",
        "category": EventCategory.ONLINE,
        "coordinator": "Karthik Menon",
        "coord_contact": "+91 98765 43215",
        "fee": Decimal("200.00"),
        "venue": "Online (KRATOS CTF Portal & Discord)",
        "capacity": 200,
        "whatsapp_group_available": True,
        "starts_at": datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc),
        "ends_at": datetime(2026, 10, 17, 21, 0, tzinfo=timezone.utc),
        "slot": EventSlot.FULL_DAY,
        "status": EventStatus.OPEN,
        "registration_open": True,
        "spots_remaining": 145,
        "team_min_size": 1,
        "team_max_size": 3,
        "required_member_count": 1,
        "substitute_count": 0,
        "allow_individual": True,
        "registration_mode": RegistrationMode.TEAM_OR_INDIVIDUAL,
        "capacity_type": CapacityType.TEAMS,
        "member_registration_mode": MemberRegistrationMode.SELF_ENTRY,
        "allow_team_invite_flow": True,
        "requires_qr_checkin": False,
        "custom_fields": {"ctf_handle": "Handle/Username"},
    },
    {
        "id": uuid.UUID("10000000-0000-0000-0000-000000000007"),
        "name": "Battle of the Bands",
        "tagline": "Amps on 11, Riffs Unleashed",
        "short_desc": "Live acoustic and electric rock/fusion competition under stadium spotlights.",
        "long_desc": "Bands battle on the grand open-air stage for cash prizes and recording studio sessions. Professional sound engineering, full drum kit, and backline amps provided.",
        "category": EventCategory.CULTURAL,
        "coordinator": "Maya Nair",
        "coord_contact": "+91 98765 43216",
        "fee": Decimal("800.00"),
        "venue": "Grand Open Air Amphitheatre",
        "capacity": 16,
        "whatsapp_group_available": True,
        "starts_at": datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc),
        "ends_at": datetime(2026, 10, 17, 22, 30, tzinfo=timezone.utc),
        "slot": EventSlot.EVENING,
        "status": EventStatus.OPEN,
        "registration_open": True,
        "spots_remaining": 6,
        "team_min_size": 3,
        "team_max_size": 8,
        "required_member_count": 3,
        "substitute_count": 1,
        "allow_individual": False,
        "registration_mode": RegistrationMode.TEAM_ONLY,
        "capacity_type": CapacityType.TEAMS,
        "member_registration_mode": MemberRegistrationMode.SELF_ENTRY,
        "allow_team_invite_flow": True,
        "requires_qr_checkin": True,
        "custom_fields": {"band_genre": "Rock / Metal / Fusion", "equipment_requirements": "Special needs"},
    },
    {
        "id": uuid.UUID("10000000-0000-0000-0000-000000000008"),
        "name": "Street Dance Face-Off",
        "tagline": "Rhythm, Attitude & Freestyle Power",
        "short_desc": "1v1 and Crew vs Crew street dance, popping, locking, and breaking battles.",
        "long_desc": "DJ on the decks dropping spontaneous beats. Dancers enter the center cypher with 60-second rounds, evaluated by celebrity judges on musicality, originality, and execution.",
        "category": EventCategory.CULTURAL,
        "coordinator": "Dev Kapoor",
        "coord_contact": "+91 98765 43217",
        "fee": Decimal("300.00"),
        "venue": "Central Student Activity Center",
        "capacity": 50,
        "whatsapp_group_available": True,
        "starts_at": datetime(2026, 10, 16, 16, 0, tzinfo=timezone.utc),
        "ends_at": datetime(2026, 10, 16, 20, 0, tzinfo=timezone.utc),
        "slot": EventSlot.EVENING,
        "status": EventStatus.OPEN,
        "registration_open": True,
        "spots_remaining": 27,
        "team_min_size": 1,
        "team_max_size": 6,
        "required_member_count": 1,
        "substitute_count": 0,
        "allow_individual": True,
        "registration_mode": RegistrationMode.TEAM_OR_INDIVIDUAL,
        "capacity_type": CapacityType.TEAMS,
        "member_registration_mode": MemberRegistrationMode.SELF_ENTRY,
        "allow_team_invite_flow": True,
        "requires_qr_checkin": True,
        "custom_fields": {"dance_style": "Breaking / Popping / Krump / All-Styles"},
    },
]


def get_sample_event_list() -> List[EventListItem]:
    return [
        EventListItem(
            id=d["id"],
            name=d["name"],
            tagline=d["tagline"],
            short_desc=d["short_desc"],
            category=d["category"],
            fee=d["fee"],
            venue=d["venue"],
            starts_at=d["starts_at"],
            ends_at=d["ends_at"],
            slot=d["slot"],
            status=d["status"],
            registration_open=d["registration_open"],
            allow_individual=d["allow_individual"],
            team_min_size=d["team_min_size"],
            team_max_size=d["team_max_size"],
            required_member_count=d["required_member_count"],
            substitute_count=d["substitute_count"],
        )
        for d in DEMO_EVENTS_DATA
    ]


def get_sample_event_detail(event_id: uuid.UUID) -> Optional[EventDetail]:
    for d in DEMO_EVENTS_DATA:
        if d["id"] == event_id:
            return EventDetail(
                id=d["id"],
                name=d["name"],
                tagline=d["tagline"],
                short_desc=d["short_desc"],
                long_desc=d["long_desc"],
                category=d["category"],
                coordinator=d["coordinator"],
                coord_contact=d["coord_contact"],
                fee=d["fee"],
                venue=d["venue"],
                capacity=d["capacity"],
                whatsapp_group_available=d["whatsapp_group_available"],
                starts_at=d["starts_at"],
                ends_at=d["ends_at"],
                slot=d["slot"],
                status=d["status"],
                registration_open=d["registration_open"],
                spots_remaining=d["spots_remaining"],
                team_min_size=d["team_min_size"],
                team_max_size=d["team_max_size"],
                required_member_count=d["required_member_count"],
                substitute_count=d["substitute_count"],
                allow_individual=d["allow_individual"],
                registration_mode=d["registration_mode"],
                capacity_type=d["capacity_type"],
                member_registration_mode=d["member_registration_mode"],
                allow_team_invite_flow=d["allow_team_invite_flow"],
                requires_qr_checkin=d["requires_qr_checkin"],
                custom_fields=d["custom_fields"],
                registration_opens_at=None,
                registration_closes_at=None,
            )
    return None


_DEV_REGISTRATIONS: dict[uuid.UUID, RegistrationOut] = {}


def create_dev_registration(event_id: uuid.UUID, profile_id: uuid.UUID, payload) -> RegistrationOut:
    reg_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    team_out = None
    if getattr(payload, "registration_type", None) == "TEAM" or getattr(payload, "registration_type", None) == RegistrationMode.TEAM_ONLY or getattr(payload, "team_name", None):
        team_id = uuid.uuid4()
        member_id = uuid.uuid4()
        from app.models.enums import TeamMemberRole, TeamMemberStatus, TeamStatus
        from app.schemas.registration import TeamMemberOut, TeamOut

        leader_member = TeamMemberOut(
            id=member_id,
            profile_id=profile_id,
            role=TeamMemberRole.LEADER,
            status=TeamMemberStatus.PENDING_PAYMENT,
            joined_at=now,
        )
        team_out = TeamOut(
            id=team_id,
            name=payload.team_name or "Team Alpha",
            leader_profile_id=profile_id,
            status=TeamStatus.FORMING,
            members=[leader_member],
        )

    from app.models.enums import RegistrationStatus
    from app.schemas.registration import RegistrationOut

    reg = RegistrationOut(
        id=reg_id,
        event_id=event_id,
        status=RegistrationStatus.PENDING,
        created_at=now,
        profile_id=profile_id if not team_out else None,
        team=team_out,
        payment=None,
    )
    _DEV_REGISTRATIONS[reg_id] = reg
    return reg


def get_dev_registration(registration_id: uuid.UUID) -> Optional[RegistrationOut]:
    return _DEV_REGISTRATIONS.get(registration_id)


def list_dev_registrations(profile_id: uuid.UUID) -> List[RegistrationOut]:
    regs = []
    for r in _DEV_REGISTRATIONS.values():
        if r.profile_id == profile_id:
            regs.append(r)
        elif r.team and (r.team.leader_profile_id == profile_id or any(m.profile_id == profile_id for m in r.team.members)):
            regs.append(r)
    return regs


def cancel_dev_registration(registration_id: uuid.UUID) -> Optional[RegistrationOut]:
    reg = _DEV_REGISTRATIONS.get(registration_id)
    if reg:
        from app.models.enums import RegistrationStatus, TeamStatus
        reg.status = RegistrationStatus.CANCELLED
        if reg.team:
            reg.team.status = TeamStatus.CANCELLED
    return reg
