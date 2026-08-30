DOCTEUR — Intégration de Semgrep

Objectif

DOCTEUR pourra utiliser Semgrep comme moteur externe d’analyse de sécurité et de qualité du code.

Semgrep analysera le code de NéronOS afin de détecter les vulnérabilités, mauvaises pratiques, secrets exposés et patterns de code dangereux.

Positionnement

Semgrep reste un outil indépendant. DOCTEUR orchestre son utilisation, interprète ses résultats et les intègre dans son diagnostic global.

DOCTEUR
   │
   ├── Health Check
   ├── Tests
   ├── Qualité du code
   └── Security
          └── Semgrep

Évolution prévue

L’intégration pourra évoluer afin que DOCTEUR combine plusieurs moteurs d’analyse et produise un verdict de santé, qualité et sécurité global pour NéronOS.

Cette intégration sera réalisée lorsque l’architecture principale de DOCTEUR sera suffisamment stabilisée.
