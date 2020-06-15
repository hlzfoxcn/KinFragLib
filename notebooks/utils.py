"""
Utility functions to work with the KinFragLib fragment library.
"""

from itertools import combinations

from bravado.client import SwaggerClient
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from rdkit import Chem, DataStructs
from rdkit.Chem import AllChem, Draw, QED, PandasTools
from rdkit.Chem.Draw import IPythonConsole
from rdkit.Chem import rdFingerprintGenerator, Descriptors, Lipinski
from rdkit.ML.Cluster import Butina
import seaborn as sns

import klifs_utils

SUBPOCKET_COLORS = {
    'AP': 'purple', 
    'FP': 'forestgreen', 
    'SE': 'c', 
    'GA': 'tab:orange', 
    'B1': 'tab:blue', 
    'B2': 'darkslateblue', 
    'X': 'grey'
}

def read_fragment_library(path_to_lib):
    """
    Read fragment library from sdf files (one file per subpocket).
    
    Parameters
    ----------
    path_to_lib : str
        Path to fragment library folder.
    
    
    Returns
    -------
    dict of pandas.DataFrame
        Fragment details, i.e. SMILES, kinase groups, and fragment RDKit molecules, (values) for each subpocket (key).
    """
    # list of folders for each subpocket
    subpockets = ['AP', 'FP', 'SE', 'GA', 'B1', 'B2', 'X']
    
    data = {}

    # iterate over subpockets
    for subpocket in subpockets:

    	data[subpocket] = _read_subpocket_fragments(subpocket, path_to_lib)
        
    return data

def _read_subpocket_fragments(subpocket, path_to_lib):
    """
    Read fragments for input subpocket.
    
    Parameters
    ----------
    subpocket : str
        Subpocket name, i.e. AP, SE, FP, GA, B1, or B2.
    path_to_lib : str
        Path to fragment library folder.
    
    Returns
    -------
    pandas.DataFrame
        Fragment details, i.e. SMILES, kinase groups, and fragment RDKit molecules, for input subpocket.
    """

    mol_supplier = Chem.SDMolSupplier(str(path_to_lib / f'{subpocket}.sdf'), removeHs=False)
        
    data = []

    for mol in mol_supplier:
        
        # Replace dummy atoms with hydrogens in fragments
        dummy = Chem.MolFromSmiles('*')
        hydrogen = Chem.MolFromSmiles('[H]', sanitize=False)
        mol_wo_dummy = AllChem.ReplaceSubstructs(mol, dummy, hydrogen, replaceAll=True)[0]
        
        # Remove all hydrogens but explicit hydrogens
        mol_wo_dummy = Chem.RemoveHs(mol_wo_dummy)      
        mol_w_dummy = Chem.RemoveHs(mol)
        
        # Generate SMILES
        smiles_wo_dummy = Chem.MolToSmiles(mol_wo_dummy)
        smiles_w_dummy = Chem.MolToSmiles(mol_w_dummy)
        
        # 2D coordinates
        AllChem.Compute2DCoords(mol_wo_dummy)
        AllChem.Compute2DCoords(mol_w_dummy)
        
        # Add property information stored for each fragment, e.g. kinase group
        data.append(
            [
                mol_wo_dummy,
                mol_w_dummy,
                mol,
                mol_w_dummy.GetProp('kinase'),
                mol_w_dummy.GetProp('family'),
                mol_w_dummy.GetProp('group'),
                mol_w_dummy.GetProp('complex_pdb'),
                mol_w_dummy.GetProp('ligand_pdb'),
                mol_w_dummy.GetProp('alt'),
                mol_w_dummy.GetProp('chain'),
                mol_w_dummy.GetProp('atom.prop.subpocket'),
                mol_w_dummy.GetProp('atom.prop.environment'),
                smiles_wo_dummy,
                smiles_w_dummy
            ]
        )
        
    fragment_library = pd.DataFrame(
        data,
        columns='ROMol ROMol_dummy ROMol_original kinase family group complex_pdb ligand_pdb alt chain atom_subpockets atom_environments smiles smiles_dummy'.split()
    )
    fragment_library['subpocket'] = subpocket

    return fragment_library


def get_original_ligands(fragment_library_concat):
    """
    Get ligands from which the fragment library originated from, 
    including each ligand's occupied subpockets, RDKit molecule (remote KLIFS access) and SMILES (from RDKit molecule).
    
    Parameters
    ----------
    fragment_library_concat : pandas.DataFrame
        Fragment library data for one or multiple subpockets.
    
    Returns
    -------
    pandas.DataFrame
        Original ligand data, including kinase, structure and fragment subpocket data.
    """

    # Get ligands from which the fragment library originated from, 
    # while collecting each ligand's occupied subpockets.
    original_ligands = pd.concat(
        [
            fragment_library_concat.groupby(['complex_pdb', 'ligand_pdb'])['subpocket'].apply(list),
            fragment_library_concat.groupby(['complex_pdb', 'ligand_pdb']).first().drop(
                ['subpocket', 'smiles', 'smiles_dummy', 'ROMol', 'ROMol_dummy', 'ROMol_original', 'atom_subpockets', 'atom_environments'], 
                axis=1
            )
        ],
        axis=1
    ).reset_index()

    # Get structures (metadata) for original ligands (takes a couple of minutes)
    structures = pd.concat(
        [
            klifs_utils.remote.structures.structures_from_pdb_id(
                original_ligand.complex_pdb,
                alt=original_ligand.alt,
                chain=original_ligand.chain
            ) 
            for index, original_ligand in original_ligands.iterrows()
        ]
    )
    
    # Get aC-helix conformation of structure
    original_ligands['ac_helix'] = structures.aC_helix.to_list()

    # Get structure IDs for original ligands
    structure_ids = structures.structure_ID
    
    # Get RDKit molecules for original ligands (takes a couple of minutes)
    original_ligands['ROMol'] = [
        klifs_utils.remote.coordinates.ligand.mol2_to_rdkit_mol(structure_id) for structure_id in structure_ids
    ]

    # Get all SMILES for original ligands (generate SMILES from RdKit molecule)
    original_ligands['smiles'] = [
        Chem.MolToSmiles(mol) for mol in original_ligands.ROMol
    ]

    return original_ligands


def get_most_common_fragments(fragments, top_x=50):
    """
    Get most common fragments.
    
    Parameters
    ----------
    fragments : pandas.DataFrame
        Fragment details, i.e. SMILES, kinase groups, and fragment RDKit molecules, for input subpocket.
    top_x : int
        Top x most common fragments.
        
    Returns
    -------
    pandas.DataFrame
        Most common fragments (sorted in descending order), including fragments' SMILES, ROMol, and count.
    """
    
    # Get number of occurrences (count) per fragment (based on SMILES) in decending order
    fragment_counts = fragments.smiles.value_counts()
    fragment_counts.name = 'fragment_count'

    # Cast Series to DataFrame and add ROMol column
    fragment_counts = fragment_counts.reset_index().rename(columns={'index': 'smiles'})
    PandasTools.AddMoleculeColumnToFrame(fragment_counts, 'smiles')

    # Sort fragments by their count (descending)
    fragment_counts.sort_values('fragment_count', ascending=False, inplace=True)
    fragment_counts.reset_index(inplace=True, drop=True)
    
    # Set molecule ID as index name
    fragment_counts.index.name = 'molecule_id'

    # Get the top X most common fragments
    if fragment_counts.shape[0] < top_x:
        
        # Select all fragments if there are less than top X fragments in subpocket
        most_common_fragments = fragment_counts
        
    else: 
        
        # If multiple fragments have the same count but some make it into the top X and some not,
        # include the latter also
    
        # Get lowest fragment count that is included in top X fragments
        lowest_fragment_count = fragment_counts.iloc[top_x-1].fragment_count

        # Get all fragments with more or equal to the lowest fragment count
        most_common_fragments = fragment_counts[
            fragment_counts.fragment_count >= lowest_fragment_count
        ]
    
    return most_common_fragments
    

def _generate_fingerprints(mols):
    """
    Generate RDKit fingerprint from list of molecules.
    
    Parameters
    ----------
    mols : list of rdkit.Chem.rdchem.Mol
        List of molecules.
        
    Returns
    -------
    list of rdkit.DataStructs.cDataStructs.ExplicitBitVect
        List of fingerprints.
    """
    
    rdkit_gen = rdFingerprintGenerator.GetRDKitFPGenerator(maxPath=5)
    fingerprints = [rdkit_gen.GetFingerprint(mol) for mol in mols]
    
    return fingerprints

def cluster_molecules(mols, cutoff=0.6):
    """
    Cluster molecules by fingerprint distance using the Butina algorithm.
    
    Parameters
    ----------
    mols : list of rdkit.Chem.rdchem.Mol
        List of molecules.
    cutoff : float
        Distance cutoff Butina clustering.
        
    Returns
    -------
    pandas.DataFrame
        Table with cluster ID - molecule ID pairs.
    """
    
    # Generate fingerprints
    fingerprints = _generate_fingerprints(mols)
    
    # Calculate Tanimoto distance matrix
    distance_matrix = _get_tanimoto_distance_matrix(fingerprints)
    
    # Now cluster the data with the implemented Butina algorithm
    clusters = Butina.ClusterData(
        distance_matrix,
        len(fingerprints),
        cutoff,
        isDistData=True
    )
    
    # Sort clusters by size
    clusters = sorted(clusters, key=len, reverse=True)
    
    # Get cluster ID - molecule ID pairs
    clustered_molecules = []

    for cluster_id, molecule_ids in enumerate(clusters, start=1):

        for cluster_member_id, molecule_id in enumerate(molecule_ids, start=1):
            clustered_molecules.append([cluster_id, cluster_member_id, molecule_id])

    clustered_molecules = pd.DataFrame(clustered_molecules, columns=['cluster_id', 'cluster_member_id', 'molecule_id'])
    
    # Print details on clustering
    print("Number of molecules:", len(fingerprints))    
    print("Threshold: ", cutoff)
    print("Number of clusters: ", len(clusters))
    print("# Clusters with only 1 molecule: ", len([cluster for cluster in clusters if len(cluster) == 1]))
    print("# Clusters with more than 5 molecules: ", len([cluster for cluster in clusters if len(cluster) > 5]))
    print("# Clusters with more than 25 molecules: ", len([cluster for cluster in clusters if len(cluster) > 25]))
    print("# Clusters with more than 100 molecules: ", len([cluster for cluster in clusters if len(cluster) > 100]))
    
    return clustered_molecules

def _get_tanimoto_distance_matrix(fingerprints):
    """
    Calculate distance matrix for list of fingerprints.
    
    Parameters
    ----------
    fingerprints : list of rdkit.DataStructs.cDataStructs.ExplicitBitVect
        List of fingerprints.
        
    Returns
    -------
    list of floats
        Distance matrix (a triangular distance matrix in the form of a list)
    """
    
    fingerprints = list(fingerprints)
    distance_matrix = []
    
    for i in range(1,len(fingerprints)):
        
        # Calculate Tanimoto similarity between fingerprints
        similarities = DataStructs.BulkTanimotoSimilarity(fingerprints[i], fingerprints[:i])
        
        # Since we need a distance matrix, calculate 1-x for every element in similarity matrix
        distance_matrix.extend([1-x for x in similarities])
    
    return distance_matrix


def get_fragmented_ligand(fragment_library, complex_pdb, ligand_pdb):
    """
    Get fragments with subpocket assignment for ligand by PDB ID.
    
    Parameters
    ----------
    fragment_library : dict of pandas.DataFrame
        Fragment details, i.e. SMILES, and fragment RDKit molecules, KLIFS and fragmentation details (values)
        for each subpocket (key).
    complex_pdb : str
        PDB ID for structure with ligand of interest.
    ligand_pdb : str
        PDB ID for ligand of interest.
    
    Returns
    -------
    PIL.PngImagePlugin.PngImageFile
        Fragmented ligand.
    """
    
    subpockets = ['SE', 'AP', 'GA', 'B1', 'B2', 'FP', 'X']  # order taken from paper Figure 4

    fragments = []

    for subpocket in subpockets:
        
        subpocket_fragments = fragment_library[subpocket]
        subpocket_fragments_selected = subpocket_fragments[
            (subpocket_fragments.complex_pdb == complex_pdb) & (subpocket_fragments.ligand_pdb == ligand_pdb)
        ].copy()
        
        subpocket_fragments_selected['subpocket'] = subpocket
        fragments.append(subpocket_fragments_selected)

    fragmented_ligand = pd.concat(fragments)
    
    return fragmented_ligand


def draw_fragmented_ligand(fragment_library, complex_pdb, ligand_pdb, mols_per_row=6):
    """
    Show fragments with subpocket assignment for ligand by PDB ID.
    
    Parameters
    ----------
    fragment_library : dict of pandas.DataFrame
        Fragment details, i.e. SMILES, and fragment RDKit molecules, KLIFS and fragmentation details (values)
        for each subpocket (key).
    complex_pdb : str
        PDB ID for structure with ligand of interest.
    ligand_pdb : str
        PDB ID for ligand of interest.
    
    Returns
    -------
    PIL.PngImagePlugin.PngImageFile
        Fragmented ligand.
    """
    
    fragmented_ligand = get_fragmented_ligand(fragment_library, complex_pdb, ligand_pdb)
    
    img = Draw.MolsToGridImage(
        fragmented_ligand.ROMol.tolist(), 
        legends=fragmented_ligand.subpocket.tolist(), 
        molsPerRow=mols_per_row
    )
    
    return img


def _get_descriptors_from_mol(mol):
    """
    Get descriptors for a molecule, i.e. number of hydrogen bond acceptors/donors, logP, and number of heavy atoms.

    Parameters
    ----------
    mol : rdkit.Chem.rdchem.Mol
        Molecule.

    Returns
    -------
    pd.Series
        Descriptors for input molecule.
    """

    smiles = Chem.MolToSmiles(mol)
    hbd = Lipinski.NumHDonors(mol)
    hba = Lipinski.NumHAcceptors(mol)
    logp = Descriptors.MolLogP(mol)
    size = mol.GetNumHeavyAtoms()

    return pd.Series([smiles, mol, hbd, hba, logp, size], index='smiles mol hbd hba logp size'.split())


def get_descriptors_from_smiles(smiles):
    """
    Get descriptors for a set of SMILES.

    Parameters
    ----------
    smiles : pd.Series
        Set of SMILES.

    Returns
    -------
    pd.Series
        Descriptors for set of SMILES.
    """

    descriptors = pd.DataFrame(
        smiles.apply(
            lambda x: _get_descriptors_from_mol(Chem.MolFromSmiles(x))
        )
    )

    return descriptors


def get_descriptors_by_fragments(fragment_library):
    """
    Get physicochemical properties of fragment library, i.e. size (# heavy atoms), logP, hydrogen bond donors and acceptors,
    after deduplicating fragments per subpocket based on their smiles.
    
    Parameters
    ----------
    fragment_library : dict of pandas.DataFrame
        SMILES and RDKit molecules for fragments (values) per subpocket (key).
    Returns
    -------
    pandas.DataFrame
        Properties of fragment library.
    """
    
    descriptors = {}

    for subpocket, fragments in fragment_library.items():
        
        # Deduplicate SMILES per subpocket
        fragments = fragments.drop_duplicates('smiles').copy()
        
        # Get descriptors for subpocket
        descriptors[subpocket] = fragments.apply(
            lambda x: _get_descriptors_from_mol(x.ROMol),
            axis=1
        )

    descriptors = pd.concat(descriptors).reset_index()

    descriptors.drop('level_1', axis=1, inplace=True)
    descriptors.rename(
        columns={
            'level_0': 'subpocket',
            'size': '# Heavy atoms',
            'logp': 'LogP',
            'hbd': '# HBD',
            'hba': '# HBA'
        },
        inplace=True
    )
    return descriptors


def get_ro5_from_mol(mol):
    """
    Get Lipinski's rule of five criteria for a molecule, i.e. molecular weight, logP, number of hydrogen bond acceptors/donors and
    accordance to Lipinski's rule of five.
    (Takes about 1s for 2000 mols.)

    Parameters
    ----------
    mol : rdkit.Chem.rdchem.Mol
        Molecule.

    Returns
    -------
    pd.Series
        Rule of five criteria for input molecule.
    """

    mw = 1 if Descriptors.ExactMolWt(mol) <= 500 else 0
    logp = 1 if Descriptors.MolLogP(mol) <= 5 else 0
    hbd = 1 if Lipinski.NumHDonors(mol) <= 5 else 0
    hba = 1 if Lipinski.NumHAcceptors(mol) <= 10 else 0
    lipinski = 1 if mw + logp + hbd + hba >= 3 else 0

    return pd.Series([mw, logp, hbd, hba, lipinski], index='mw logp hbd hba lipinski'.split())


def get_ro3_from_mol(mol):
    """
    Get rule of three criteria for a fragment, i.e. molecular weight, logP, number of hydrogen bond acceptors/donors, number of rotatable bonds, and PSA.

    Parameters
    ----------
    mol : rdkit.Chem.rdchem.Mol
        Fragment.

    Returns
    -------
    pd.Series
        Rule of three criteria for input fragment.
        
    Notes
    -----
    Taken from: https://europepmc.org/article/med/14554012
    """
    
    properties = QED.properties(mol)

    mw = 1 if properties.MW < 300 else 0
    logp = 1 if properties.ALOGP <= 3 else 0
    hbd = 1 if properties.HBD <= 3 else 0
    hba = 1 if properties.HBA <= 3 else 0
    nrot = 1 if properties.ROTB <=3 else 0
    psa = 1 if properties.PSA <= 60 else 0

    return pd.Series([mw, logp, hbd, hba, nrot, psa], index='mw logp hbd hba nrot psa'.split())


def get_ro5_from_smiles(smiles):
    """
    Get Lipinski's rule of five criteria for a set of SMILES.

    Parameters
    ----------
    smiles : pd.Series
        Set of SMILES.

    Returns
    -------
    pd.Series
        Ratio of molecules that fulfill Lipinski's rule of five.
    """

    drug_likeness = pd.DataFrame(
        smiles.apply(
            lambda x: get_ro5_from_mol(Chem.MolFromSmiles(x))
        )
    )
    print(f'Number of molecules: {drug_likeness.shape[0]}')

    drug_likeness_ratio = round(drug_likeness.apply(sum) / len(drug_likeness) * 100, 0)

    return drug_likeness_ratio


def get_connections_by_fragment(fragment_library_concat):
    """
    For each fragment, extract connecting subpockets (e.g. ['FP', 'SE'] for subpocket 'AP') and define subpocket connections (e.g. ['AP=FP', 'AP=SE']). 
    
    Parameters
    ----------
    fragment_library_concat : pandas.DataFrame
        Fragment library data for one or multiple subpockets.
        
    Returns
    -------
    pandas.DataFrame
        Fragment library data including connecting subpockets and connections.    
    """

    # For each fragment, extract connecting subpocket from atom_subpockets, e.g. ['FP', 'SE'] for subpocket 'AP'
    fragment_library_concat['connections'] = fragment_library_concat.apply(
        lambda x: _get_connecting_subpockets(x.subpocket, x.atom_subpockets.split()), 
        axis=1
    )
    
    # Extract each connection (join connecting subpockets), e.g. ['AP=FP', 'AP=SE']
    fragment_library_concat['connections_name'] = fragment_library_concat.apply(
        lambda x: ["=".join(sorted([x.subpocket, i])) for i in x.connections], 
        axis=1
    )

    return fragment_library_concat['kinase complex_pdb ligand_pdb atom_subpockets connections connections_name subpocket'.split()]


def _get_connecting_subpockets(subpocket, atom_subpockets):
    """
    Get a fragment's connecting subpockets based on the fragment's subpocket and all fragment atoms' subpockets (only dummy atoms will have differing subpockets).
    
    Parameters
    ----------
    subpocket : str
        Fragment's subpocket.
    atom_subpockets : list of str
        Fragment atoms' subpockets.
        
    Returns
    -------
    list of str
        Dummy atoms' subpockets (i.e. the subpockets that the fragment is connected to)
    """
    
    if subpocket != 'X':
        return [i if i[0] != 'X' else i[0] for i in atom_subpockets if i != subpocket]
    else:
        return [i for i in atom_subpockets if i[0] != subpocket]


def get_connections_count_by_ligand(connections_by_ligand):
    """
    Count subpocket connections (by type) across all ligands, i.e. how often a specific connection appears in the data set.
    
    Parameters
    ----------
    connections_by_ligand : pandas.DataFrame
        Ligands represented by fragment library with details on their subpocket connections (see connections_by_ligand() function). 
        
    Returns
    -------
    pandas.DataFrame
        Subpocket connections count and frequency across all ligands.
    """
    
    # For each ligand (row) count connection type (column)
    connection_matrix = pd.DataFrame({i: [] for i in connections_by_ligand.index}).transpose()

    for index, row in connections_by_ligand.iteritems():

        for connection in row:

            if connection not in connection_matrix.columns:
                connection_matrix[connection] = 0


            connection_matrix[connection][index] += 1
            
    # Count connection types per ligand
    connections_count = pd.DataFrame(
        {
            'count': connection_matrix.sum(), 
            'frequency': round(connection_matrix.sum() / connection_matrix.shape[0] * 100, 1)
        }
    ).sort_values('count', ascending=False)

    return connections_count


def get_fragment_similarity_per_subpocket(fragment_library_concat):
    """
    Calculate fingerprint similarities for all pairwise fragment combinations within each subpocket,
    after deduplicating fragments per subpocket based on their smiles.
    
    Parameters
    ----------
    fragment_library_concat : pandas.DataFrame
        Fragment library data for one or multiple subpockets.
        
    Returns
    -------
    pandas.DataFrame
        Similarity values for all pairwise fragment combinations within each subpocket.
    """
    
    similarities_all = []

    for subpocket, fragments in fragment_library_concat.groupby('subpocket', sort=False):

        smiles_deduplicated = fragments['smiles'].drop_duplicates()

        mols = smiles_deduplicated.apply(lambda x: Chem.MolFromSmiles(x))
        fingerprints = _generate_fingerprints(mols)

        similarities = []

        for fp1, fp2 in combinations(fingerprints, 2):
            similarities.append(DataStructs.FingerprintSimilarity(fp1, fp2))
            
        similarities = pd.DataFrame(similarities)
        similarities.rename(columns={0: 'similarity'}, inplace=True)
        similarities['subpocket'] = subpocket

        similarities_all.append(similarities)
        
    similarities_all = pd.concat(similarities_all)

    return similarities_all


def get_fragment_similarity_per_kinase_group(fragment_library_concat):
    """
    Calculate fingerprint similarities for all pairwise fragment combinations within each kinase group and subpocket
    after deduplicating fragments per subpocket and kinase group based on their smiles.
    
    Parameters
    ----------
    fragment_library_concat : pandas.DataFrame
        Fragment library data for one or multiple subpockets.
        
    Returns
    -------
    pandas.DataFrame
        Similarity values for all pairwise fragment combinations within each kinase group and subpocket.
    """
    
    similarities_all = []

    for group, fragments in fragment_library_concat.groupby(['group', 'subpocket']):

        # Group and deduplicate fragments by kinase group and subpockets
        fragments_deduplicated = fragments.drop_duplicates('smiles')

        fingerprints = _generate_fingerprints(fragments_deduplicated.ROMol)

        similarities = []

        for fp1, fp2 in combinations(fingerprints, 2):
            similarities.append(DataStructs.FingerprintSimilarity(fp1, fp2))

        similarities = pd.DataFrame(similarities)
        similarities.rename(columns={0: 'similarity'}, inplace=True)
        similarities['group'] = group[0]
        similarities['subpocket'] = group[1]

        similarities_all.append(similarities)
    
    similarities_all = pd.concat(similarities_all)
    
    # Add subpocket 'Total' for similarites which were calculated between fragments within each kinase group and subpockt
    similarities_total = similarities_all.copy()
    similarities_total['group'] = 'Total'
    
    similarities_all = pd.concat([similarities_all, similarities_total])
    
    return similarities_all


def plot_n_subpockets(n_subpockets_per_ligand_distribution):
    """
    Plot number of subpockets occupied across all ligands.
    """

    plt.figure(figsize=(8,8))
    plt.bar(
        n_subpockets_per_ligand_distribution.index, 
        n_subpockets_per_ligand_distribution.ligand_count
    )
    plt.ylabel('# Ligands', fontsize=17)
    plt.xlabel('# Subpockets', fontsize=17)
    plt.yticks(fontsize=17)
    plt.xticks(fontsize=17)
    
    plt.savefig(f'figures/n_subpockets.png', dpi=300, bbox_inches='tight')
    
    
def plot_n_fragments_per_subpocket(n_fragments_per_subpocket, n_fragments_per_subpocket_deduplicated):
    """
    Plot number of fragments and deduplicated fragments per subpocket.
    """
    
    plt.figure(figsize=(8,8))
    ax1 = plt.bar(
        SUBPOCKET_COLORS.keys(), 
        n_fragments_per_subpocket, 
        fill=False, 
        edgecolor=SUBPOCKET_COLORS.values()
    )
    ax2 = plt.bar(
        SUBPOCKET_COLORS.keys(), 
        n_fragments_per_subpocket_deduplicated, 
        color=SUBPOCKET_COLORS.values()
    )
    plt.legend(['All fragments', 'Deduplicated\nfragments'], fontsize=17)
    plt.ylabel('# Fragments', fontsize=17)
    plt.xlabel('Subpocket', fontsize=17)
    plt.xticks(fontsize=17)
    plt.yticks(fontsize=17)
    
    # Add percentages to bars
    bars = ax1.patches
    bar_labels = [
        str(int(round((i-j)/i*100, 0))) for i, j in zip(
            n_fragments_per_subpocket, 
            n_fragments_per_subpocket_deduplicated
        )
    ]
    for bar, label in zip(bars, bar_labels):

        plt.text(
            bar.get_x() + bar.get_width() / 2, 
            bar.get_height(),
            label, 
            ha='center', 
            va='bottom', 
            fontsize=17,
            color='black'
        )
    
    plt.savefig(f'figures/n_fragments_per_subpocket.png', dpi=300, bbox_inches='tight')


def plot_fragment_similarity(similarities_by_group, group_name):
    """
    Plot fragment similarity by category, such as subpocket or kinase group.
    """
    
    plt.figure(figsize=(9,9))
    
    try:
        ax = sns.boxplot(
            x=similarities_by_group.columns[1], 
            y=similarities_by_group.columns[0], 
            data=similarities_by_group, 
            palette=SUBPOCKET_COLORS
        )
    except KeyError:
        ax = sns.boxplot(
        x=similarities_by_group.columns[1], 
        y=similarities_by_group.columns[0], 
        data=similarities_by_group, 
        color='dodgerblue'
    )
    plt.ylabel('Tanimoto similarity', fontsize=18)
    plt.xlabel(group_name, fontsize=18)
    plt.xticks(fontsize=18)
    plt.yticks(fontsize=18)
    
    plt.savefig(f'figures/similarities_by_{group_name.lower().replace(" ", "_")}.png', dpi=300)
    
    
def plot_fragment_descriptors(descriptors):
    """
    Plot fragment descriptors.
    """
    
    plt.figure(figsize=(25,6))

    for i, descriptor_name in enumerate(descriptors.columns[3:]):

        plt.subplot(1, 4, i+1)
        sns.boxplot(
            x='subpocket', 
            y=descriptor_name, 
            data=descriptors, 
            palette=SUBPOCKET_COLORS, 
            medianprops={'linewidth':3, 'linestyle':'-'}
        )
        plt.ylabel(descriptor_name, fontsize=16)
        plt.xlabel('Subpocket', fontsize=16)
        plt.xticks(fontsize=16)
        plt.yticks(fontsize=16)
        
    plt.savefig(f'figures/descriptors.png', dpi=300)


def draw_fragments(fragments, mols_per_row=10, max_mols=50):
    """
    Draw fragments.
    
    Parameters
    ----------
    fragments : pandas.DataFrame
        Fragments (including data like complex and ligand PDB ID, chain ID, and alternate model).
    mols_per_row : int
        Number of molecules per row.
    max_mols : int
        Number of molecules displayed.

    Returns
    -------
    PIL.PngImagePlugin.PngImageFile
        Image of fragments.
    """

    image = Draw.MolsToGridImage(
        fragments.ROMol,
        molsPerRow=mols_per_row, 
        maxMols=max_mols,
        legends=fragments.apply(
            lambda x: f'{x.complex_pdb}|{x.chain}:{x.ligand_pdb}' if x.alt == ' ' else f'{x.complex_pdb}|{x.chain}|{x.alt}:{x.ligand_pdb}',
            axis=1
        ).to_list()
    )
        
    return image


def draw_ligands_from_pdb_ids(complex_pdbs, ligand_pdbs, sub_img_size=(150, 150), mols_per_row=1, max_mols=50):
    """
    Draw ligands from PDB ID (fetch data directly from KLIFS database).
    
    Parameters
    ----------
    complex_pdbs : str or list of str
        One or more complex PDB IDs.
    ligand_pdbs : str or list of str
        One or more ligand PDB IDs complementary to complex PDB IDs.
    sub_img_size : 
        Image size.
    mols_per_row : 
        Number of molecules per row.
    max_mols : int
        Number of molecules displayed.

    Returns
    -------
    PIL.PngImagePlugin.PngImageFile
        Ligand images.
    """
        
    if isinstance(complex_pdbs, str):
        complex_pdbs = [complex_pdbs]
    if isinstance(ligand_pdbs, str):
        ligand_pdbs = [ligand_pdbs]
        
    if len(complex_pdbs) != len(ligand_pdbs):
        raise ValueError(f'Complex and ligand PDB ID lists must be of same length.')
    
    KLIFS_API_DEFINITIONS = "http://klifs.vu-compmedchem.nl/swagger/swagger.json"
    KLIFS_CLIENT = SwaggerClient.from_url(KLIFS_API_DEFINITIONS, config={'validate_responses': False})

    # Get KLIFS structures by PDB ID (these include all complex PDB related details, thus multiple ligand PDBs are possible)
    structures = KLIFS_CLIENT.Structures.get_structures_pdb_list(pdb_codes=complex_pdbs).response().result
    structures = pd.DataFrame(
        [
            {
                'structure_id': structure['structure_ID'],
                'kinase': structure['kinase'],
                'complex_pdb': structure['pdb'],
                'chain': structure['chain'],
                'alt': structure['alt'],
                'ligand_pdb': structure['ligand'],
            } for structure in structures
        ]
    )
    
    # Keep only intended complex-ligand pairs (if multiple keep only first entry)
    complex_ligand_pairs = pd.DataFrame(
        {
            'complex_pdb': complex_pdbs,
            'ligand_pdb': ligand_pdbs
        }
    )
    
    # Filter database result by intended complex-ligand pairs
    structures = structures.merge(
        complex_ligand_pairs, 
        on=['complex_pdb', 'ligand_pdb']
    ).groupby(
        ['complex_pdb', 'ligand_pdb']
    ).first().reset_index()
        

    mols = []
    legends = []

    for index, structure in structures.iterrows():

        # Get ligand mol2 text
        ligand_mol2_text = KLIFS_CLIENT.Structures.get_structure_get_ligand(
                structure_ID=structure['structure_id']
        ).response().result

        # Draw ligand in 2D
        mol = Chem.MolFromMol2Block(ligand_mol2_text)
        AllChem.Compute2DCoords(mol)
        mols.append(mol)

        # Generate legend label
        legends.append(
            f'{structure["complex_pdb"]}:{structure["ligand_pdb"]}'
        )
            
    image = Draw.MolsToGridImage(
        mols,
        subImgSize=sub_img_size,
        legends=legends,
        molsPerRow=mols_per_row,
        maxMols=max_mols
    )
    
    return image
