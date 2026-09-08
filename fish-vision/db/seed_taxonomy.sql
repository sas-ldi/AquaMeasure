-- Seed taxonomy for Madagascar herbivore / surgeonfish workflow.
-- UUIDs are fixed for reproducible tests and mapping files.

INSERT OR IGNORE INTO taxon_nodes (id, parent_id, rank, scientific_name, common_name, is_provisional)
VALUES
    ('taxon-root-fish', NULL, 'provisional', 'Actinopterygii', 'Poisson', 0),
    ('taxon-fish-generic', 'taxon-root-fish', 'provisional', 'fish', 'Poisson (generique)', 0),
    ('taxon-fam-acanthuridae', 'taxon-root-fish', 'family', 'Acanthuridae', 'Chirurgiens', 0),
    ('taxon-gen-acanthurus', 'taxon-fam-acanthuridae', 'genus', 'Acanthurus', 'Chirurgien', 0),
    ('taxon-gen-ctenochaetus', 'taxon-fam-acanthuridae', 'genus', 'Ctenochaetus', 'Chirurgien-brosse', 0),
    ('taxon-gen-zebrasoma', 'taxon-fam-acanthuridae', 'genus', 'Zebrasoma', 'Chirurgien-voile', 0),
    ('taxon-sp-unknown-acanthurus', 'taxon-gen-acanthurus', 'species', 'Acanthurus sp.', 'Chirurgien non identifie', 1),
    ('taxon-sp-unknown-ctenochaetus', 'taxon-gen-ctenochaetus', 'species', 'Ctenochaetus sp.', 'Chirurgien-brosse non identifie', 1),
    -- Familles recifales (dataset Roboflow Fish / imports publics)
    ('taxon-fam-scaridae', 'taxon-root-fish', 'family', 'Scaridae', 'Poissons-perroquets', 0),
    ('taxon-fam-pomacanthidae', 'taxon-root-fish', 'family', 'Pomacanthidae', 'Poissons-anges', 0),
    ('taxon-fam-pomacentridae', 'taxon-root-fish', 'family', 'Pomacentridae', 'Demoiselles', 0),
    ('taxon-fam-labridae', 'taxon-root-fish', 'family', 'Labridae', 'Girelles', 0),
    ('taxon-fam-balistidae', 'taxon-root-fish', 'family', 'Balistidae', 'Balistes', 0),
    ('taxon-fam-carangidae', 'taxon-root-fish', 'family', 'Carangidae', 'Carangues', 0),
    ('taxon-fam-ephippidae', 'taxon-root-fish', 'family', 'Ephippidae', 'Ephippidae', 0),
    ('taxon-fam-lutjanidae', 'taxon-root-fish', 'family', 'Lutjanidae', 'Vivaneaux', 0),
    ('taxon-fam-scombridae', 'taxon-root-fish', 'family', 'Scombridae', 'Scombridae', 0),
    ('taxon-fam-serranidae', 'taxon-root-fish', 'family', 'Serranidae', 'Merous', 0),
    ('taxon-fam-zanclidae', 'taxon-root-fish', 'family', 'Zanclidae', 'Idole des Maures', 0),
    ('taxon-fam-shark', 'taxon-root-fish', 'family', 'shark', 'Requins', 0),
    ('taxon-fam-chaetodontidae', 'taxon-root-fish', 'family', 'Chaetodontidae', 'Chaetons', 0),
    ('taxon-fam-haemulidae', 'taxon-root-fish', 'family', 'Haemulidae', 'Gores', 0),
    ('taxon-fam-muraenidae', 'taxon-root-fish', 'family', 'Muraenidae', 'Murènes', 0);

INSERT OR IGNORE INTO projects (id, name, description)
VALUES ('proj-default', 'default', 'Projet par defaut pour imports et tests');
