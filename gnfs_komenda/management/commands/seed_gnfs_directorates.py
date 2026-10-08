"""
GNFS Functional Organisational Structure Seeder.
Populates verified GNFS Directorates, Departments/Divisions, and Units.
Strictly idempotent via update_or_create.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from gnfs_komenda.models import FunctionalUnit

# Authoritative Verified GNFS Functional Structure Dataset
VERIFIED_GNFS_FUNCTIONAL_STRUCTURE = [
    {
        "name": "Administration",
        "code": "ADM",
        "unit_type": FunctionalUnit.FunctionalUnitType.DIRECTORATE,
        "display_order": 10,
        "description": "General service administration, public relations, records and registry.",
        "children": [
            {
                "name": "Records & Statistics",
                "code": "ADM-RS",
                "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT,
                "display_order": 1,
                "description": "Central records management, secretariat, filing and statistics.",
                "children": [
                    {"name": "Fire Secretariat", "code": "ADM-RS-SEC", "unit_type": FunctionalUnit.FunctionalUnitType.UNIT, "display_order": 1},
                    {"name": "Filing", "code": "ADM-RS-FIL", "unit_type": FunctionalUnit.FunctionalUnitType.UNIT, "display_order": 2},
                    {"name": "Typing Pool", "code": "ADM-RS-TYP", "unit_type": FunctionalUnit.FunctionalUnitType.UNIT, "display_order": 3},
                    {"name": "Central Registry", "code": "ADM-RS-REG", "unit_type": FunctionalUnit.FunctionalUnitType.UNIT, "display_order": 4},
                ]
            },
            {
                "name": "Public Relations",
                "code": "ADM-PR",
                "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT,
                "display_order": 2,
                "description": "Corporate communications, public education, media and protocol.",
                "children": [
                    {"name": "Media Relations", "code": "ADM-PR-MED", "unit_type": FunctionalUnit.FunctionalUnitType.UNIT, "display_order": 1},
                    {"name": "Public Education", "code": "ADM-PR-EDU", "unit_type": FunctionalUnit.FunctionalUnitType.UNIT, "display_order": 2},
                    {"name": "Audio-Visual & Library", "code": "ADM-PR-AVL", "unit_type": FunctionalUnit.FunctionalUnitType.UNIT, "display_order": 3},
                    {"name": "Protocol", "code": "ADM-PR-PRO", "unit_type": FunctionalUnit.FunctionalUnitType.UNIT, "display_order": 4},
                ]
            }
        ]
    },
    {
        "name": "Operations",
        "code": "OPS",
        "unit_type": FunctionalUnit.FunctionalUnitType.DIRECTORATE,
        "display_order": 20,
        "description": "Fire fighting, rescue operations, investigations and master control.",
        "children": [
            {"name": "Investigations", "code": "OPS-INV", "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT, "display_order": 1},
            {"name": "Incident Response", "code": "OPS-INC", "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT, "display_order": 2},
            {"name": "Fire Master Control", "code": "OPS-FMC", "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT, "display_order": 3},
        ]
    },
    {
        "name": "Fire Safety",
        "code": "FS",
        "unit_type": FunctionalUnit.FunctionalUnitType.DIRECTORATE,
        "display_order": 30,
        "description": "Fire prevention, safety certifications and public fire inspections.",
        "children": [
            {"name": "Public Fire Education", "code": "FS-PFE", "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT, "display_order": 1},
            {"name": "Inspectorate", "code": "FS-INS", "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT, "display_order": 2},
            {"name": "Certification", "code": "FS-CRT", "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT, "display_order": 3},
        ]
    },
    {
        "name": "Technical Services",
        "code": "TECH",
        "unit_type": FunctionalUnit.FunctionalUnitType.DIRECTORATE,
        "display_order": 40,
        "description": "Apparatus maintenance, engineering, radio communications and workshops.",
        "children": [
            {"name": "Transport & Maintenance", "code": "TECH-TRN", "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT, "display_order": 1},
            {"name": "Radio Communication", "code": "TECH-RAD", "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT, "display_order": 2},
            {"name": "Radio Workshop", "code": "TECH-RWS", "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT, "display_order": 3},
            {"name": "Central Mechanic Workshop", "code": "TECH-CMW", "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT, "display_order": 4},
        ]
    },
    {
        "name": "Human Resource Development and Training",
        "code": "HRDT",
        "unit_type": FunctionalUnit.FunctionalUnitType.DIRECTORATE,
        "display_order": 50,
        "description": "Personnel planning, career development, training schools and academies.",
        "children": [
            {"name": "Human Resource Planning", "code": "HRDT-HRP", "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT, "display_order": 1},
            {"name": "Human Resource Development", "code": "HRDT-HRD", "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT, "display_order": 2},
        ]
    },
    {
        "name": "Logistics",
        "code": "LOG",
        "unit_type": FunctionalUnit.FunctionalUnitType.DIRECTORATE,
        "display_order": 60,
        "description": "Procurement, supply chain management, tailoring and estate management.",
        "children": [
            {
                "name": "Supply Chain Management",
                "code": "LOG-SCM",
                "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT,
                "display_order": 1,
                "children": [
                    {"name": "Tailoring", "code": "LOG-SCM-TAIL", "unit_type": FunctionalUnit.FunctionalUnitType.UNIT, "display_order": 1}
                ]
            },
            {
                "name": "Estate Management",
                "code": "LOG-EST",
                "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT,
                "display_order": 2,
                "children": [
                    {"name": "Project", "code": "LOG-EST-PROJ", "unit_type": FunctionalUnit.FunctionalUnitType.UNIT, "display_order": 1}
                ]
            }
        ]
    },
    {
        "name": "Special Duties",
        "code": "SD",
        "unit_type": FunctionalUnit.FunctionalUnitType.DIRECTORATE,
        "display_order": 70,
        "description": "Service intelligence, professional standards and corporate partnerships.",
        "children": [
            {"name": "Fire Service Intelligence & Professional Standards", "code": "SD-FSIPS", "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT, "display_order": 1},
            {"name": "Corporate & Institutional Partnerships", "code": "SD-CIP", "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT, "display_order": 2},
        ]
    },
    # Verified Directorates without authoritative published child structures
    {"name": "Finance", "code": "FIN", "unit_type": FunctionalUnit.FunctionalUnitType.DIRECTORATE, "display_order": 80, "description": "Financial management, accounting and budgetary control.", "children": []},
    {"name": "Legal", "code": "LEG", "unit_type": FunctionalUnit.FunctionalUnitType.DIRECTORATE, "display_order": 90, "description": "Legal counsel, legislative affairs and contract compliance.", "children": []},
    {"name": "Welfare", "code": "WEL", "unit_type": FunctionalUnit.FunctionalUnitType.DIRECTORATE, "display_order": 100, "description": "Personnel welfare, benefits and support services.", "children": []},
    {"name": "Policy Planning, Monitoring & Evaluation", "code": "PPME", "unit_type": FunctionalUnit.FunctionalUnitType.DIRECTORATE, "display_order": 110, "description": "Strategic planning, monitoring, evaluation and statistics.", "children": []},
    {"name": "Research / Information Technology", "code": "RIT", "unit_type": FunctionalUnit.FunctionalUnitType.DIRECTORATE, "display_order": 120, "description": "ICT systems, software infrastructure and technical research.", "children": []},
    {"name": "Rural Fires", "code": "RF", "unit_type": FunctionalUnit.FunctionalUnitType.DIRECTORATE, "display_order": 130, "description": "Bush fire prevention, community volunteer brigades and rural outreach.", "children": []},
    {"name": "Health", "code": "HLTH", "unit_type": FunctionalUnit.FunctionalUnitType.DIRECTORATE, "display_order": 140, "description": "Medical services, clinics, emergency medical technicians.", "children": []},
]


class Command(BaseCommand):
    help = "Seed verified GNFS Functional Organisational Structure (Directorates, Departments, Units)."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("=" * 65))
        self.stdout.write(self.style.SUCCESS("GNFS FUNCTIONAL ORGANISATIONAL STRUCTURE SEEDER"))
        self.stdout.write(self.style.SUCCESS("=" * 65))

        stats = {
            "directorates_created": 0,
            "directorates_updated": 0,
            "departments_created": 0,
            "departments_updated": 0,
            "units_created": 0,
            "units_updated": 0,
            "unverified_children_skipped": 0,
        }

        with transaction.atomic():
            for d_data in VERIFIED_GNFS_FUNCTIONAL_STRUCTURE:
                directorate, created = FunctionalUnit.objects.update_or_create(
                    name=d_data["name"],
                    parent=None,
                    defaults={
                        "code": d_data["code"],
                        "unit_type": d_data["unit_type"],
                        "description": d_data.get("description", ""),
                        "display_order": d_data.get("display_order", 0),
                        "active": True,
                    }
                )
                if created:
                    stats["directorates_created"] += 1
                else:
                    stats["directorates_updated"] += 1

                children = d_data.get("children", [])
                if not children:
                    stats["unverified_children_skipped"] += 1

                for dept_data in children:
                    dept, d_created = FunctionalUnit.objects.update_or_create(
                        name=dept_data["name"],
                        parent=directorate,
                        defaults={
                            "code": dept_data["code"],
                            "unit_type": dept_data["unit_type"],
                            "description": dept_data.get("description", ""),
                            "display_order": dept_data.get("display_order", 0),
                            "active": True,
                        }
                    )
                    if d_created:
                        stats["departments_created"] += 1
                    else:
                        stats["departments_updated"] += 1

                    for unit_data in dept_data.get("children", []):
                        unit, u_created = FunctionalUnit.objects.update_or_create(
                            name=unit_data["name"],
                            parent=dept,
                            defaults={
                                "code": unit_data["code"],
                                "unit_type": unit_data["unit_type"],
                                "description": unit_data.get("description", ""),
                                "display_order": unit_data.get("display_order", 0),
                                "active": True,
                            }
                        )
                        if u_created:
                            stats["units_created"] += 1
                        else:
                            stats["units_updated"] += 1

        self.stdout.write(f"\nDirectorates Processed: {stats['directorates_created'] + stats['directorates_updated']} (Created: {stats['directorates_created']}, Updated: {stats['directorates_updated']})")
        self.stdout.write(f"Departments/Divisions Processed: {stats['departments_created'] + stats['departments_updated']} (Created: {stats['departments_created']}, Updated: {stats['departments_updated']})")
        self.stdout.write(f"Units Processed: {stats['units_created'] + stats['units_updated']} (Created: {stats['units_created']}, Updated: {stats['units_updated']})")
        self.stdout.write(f"Directorates with Unverified Child Structures (flagged): {stats['unverified_children_skipped']}")
        self.stdout.write(self.style.SUCCESS("\nGNFS functional structure seeded successfully (100% idempotent)."))
