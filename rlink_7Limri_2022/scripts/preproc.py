import nibabel
import os
import scipy.io
import nipype.interfaces.spm as spm
import nipype.interfaces.spm.utils as spmu
import numpy as np
from skimage.restoration import denoise_nl_means, estimate_sigma
from nilearn import plotting
from nipype.interfaces import cat12
import subprocess
import rlink_7Limri_2022
import glob
import argparse


def denoising_nlm(image, output):
    image = nibabel.load(image)
    arr_image = image.get_fdata()
    sigma_est = np.mean(estimate_sigma(arr_image))
    arr_denoised_image = denoise_nl_means(arr_image, h=1.15 * sigma_est,
                                          patch_size=9,
                                          patch_distance=5)
    denoised_image = nibabel.Nifti1Image(arr_denoised_image, image.affine)
    nibabel.save(denoised_image, output)
    return 0


def denoising_cat12(registered_Li):
    # non-local means (SANLM)
    # denoising filter (Manjón et al., 2010)
    c = cat12.CAT12SANLMDenoising()
    c.inputs.in_files = registered_Li
    c.run()
    return 0


def coregistration(target_file, moving_file, coreg_path):
    coreg = spmu.CalcCoregAffine()
    coreg.inputs.target = target_file
    coreg.inputs.moving = moving_file
    coreg.inputs.mat = coreg_path
    coreg.inputs.use_mcr = True
    coreg.run()
    return 0


def apply_defor(defor, moving_file):
    # apply H coil to MNI
    norm12 = spm.Normalize12()
    norm12.inputs.apply_to_files = moving_file
    norm12.inputs.deformation_file = defor
    # norm12.inputs.write_bounding_box = [[-78, -112, -70], [78, 76, 85]]
    norm12.inputs.write_bounding_box = [[-90, -126, -72], [90, 90, 108]]
    norm12.inputs.write_voxel_sizes = [1.5, 1.5, 1.5]
    norm12.inputs.write_interp = 4
    norm12.inputs.jobtype = 'write'
    norm12.inputs.use_mcr = True
    # norm12.inputs.out_prefix = "MNI_"
    norm12.run()
    return 0


def apply_transform(input, matrix, output):
    applymat = spmu.ApplyTransform()
    applymat.inputs.in_file = input
    applymat.inputs.mat = matrix  # .mat
    applymat.inputs.out_file = output
    applymat.run()
    return 0


def resclice2(input, target):
    r2ref = spmu.ResliceToReference()
    r2ref.inputs.in_files = input
    r2ref.inputs.target = target
    r2ref.run()
    return 0


def resclice(input, target, output):
    r2ref = spmu.Reslice()
    r2ref.inputs.in_file = input
    r2ref.inputs.space_defining = target
    r2ref.inputs.out_file = output
    r2ref.run()
    return 0


def scale(imfile, scaledfile, scale):
    """ Scale the MRI image.
    .. note:: This function is based on FSL.
    Parameters
    ----------
    imfile: str
        the input image.
    scaledfile: str
        the path to the scaled input image.
    scale: int
        the scale factor in all directions.
    Returns
    -------
    scaledfile, trffile: str
        the generated files.
    """
    trffile = scaledfile.split(".")[0] + ".txt"
    cmd = ["flirt", "-in", imfile, "-ref", imfile, "-out",
           scaledfile, "-applyisoxfm", str(scale), "-omat", trffile]
    subprocess.check_call(cmd)
    return scaledfile, trffile


def dicom_to_nii(liste_filename):
    di = spmu.DicomImport()
    di.inputs.in_files = liste_filename
    di.run()
    return 0


def write_matlabbatch(template, nii_file, tpm_file, darteltpm_file, outfile):
    """ Complete matlab batch from template.

    Parameters
    ----------
    template: str
        path to template batch to be completed.
    nii_files: list
        the Nifti image to be processed.
    tpm_file: str
        path to the SPM TPM file.
    darteltpm_file: str
        path to the CAT12 tempalte file.
    outfile: str
        path to the generated matlab batch file that can be used to launch
        CAT12 VBM preprocessing.
    """
    nii_file_str = ""
    for i in nii_file:
        nii_file_str += "'{0}' \n".format(i)
    with open(template, "r") as of:
        stream = of.read()
    stream = stream.format(anat_file=nii_file_str, tpm_file=tpm_file,
                           darteltpm_file=darteltpm_file)
    with open(outfile, "w") as of:
        of.write(stream)
    return 0


def plot_anat_Li(li_mni_denoised, anat_MNI, threshold, figname):
    li_img = nibabel.load(li_mni_denoised)
    bg_img = nibabel.load(anat_MNI)
    arr = li_img.get_fdata()
    arr[arr < threshold] = 0
    li_img = nibabel.Nifti1Image(arr, li_img.affine)
    display = plotting.plot_stat_map(li_img, bg_img, cut_coords=(-35, 54, -44),
                                     cmap=plotting.cm.black_blue)
    display.savefig(figname)
    # plotting.show()
    display.close()
    return 0


def createPBS(prefix_PBS, c, target_Lianat, target_anat,
              moving_file_Li,
              transfo_folder,
              executable_cat12, standalone_cat12,
              mcr_matlab, matlabbatch, tpm_file,
              darteltpm_file):
    """Write the PBS file at the path prefix_PBS
    """
    subprocess.check_call(["mkdir", "-p", prefix_PBS])
    PBS_filename = os.path.join(prefix_PBS, "PBSimg{0}.sh".format(c))

    with open(PBS_filename, "w") as file:
        ligne1 = "#!/bin/sh"
        ligne2 = "#PBS -q Nspin_long"  # Cati_long Nspin_bigM, Nspin_long
        ligne3 = "#PBS -l walltime=80:00:00"  # 200:00:00
        ligne4 = "#PBS -N limri{NUMBER}".format(NUMBER=c)
        ligne5 = "#PBS -o {0}/stdout.txt".format(prefix_PBS)
        ligne6 = "#PBS -e {0}/stderr.txt".format(prefix_PBS)
        ligne7 = ("python3 "
                  "/volatile/7Li/git/rlink_7Limri_2022/rlink_7Limri_2022"\
                  "/scripts/preproc.py " 
                  "--method regex "
                  "--Li {Li} "
                  "--Lianat {Lianat} "
                  "--anat {anat} "
                  "--launch local"
                  ).format(Li=moving_file_Li,
                           Lianat=target_Lianat, anat=target_anat)
        file.write(ligne1)
        file.write("\n")
        file.write(ligne2)
        file.write("\n")
        file.write(ligne3)
        file.write("\n")
        file.write(ligne4)
        file.write("\n")
        file.write(ligne5)
        file.write("\n")
        file.write(ligne6)
        file.write("\n")
        file.write(ligne7)

    return PBS_filename


def launch_cluster(PBS_filename):
    cmd = ["qsub", PBS_filename]
    subprocess.Popen(cmd)
    return 0


def get_list_from_txt(path):
    with open(path) as flux:
        lignes = flux.readlines()
    lignes = [i.replace("\n", "") for i in lignes]
    return lignes


def find_threshold(mask_MNI, li_mni_denoised):
    li_img = nibabel.load(li_mni_denoised)
    mask_img = nibabel.load(mask_MNI)
    arr = li_img.get_fdata()
    arr[mask_img.get_fdata() != 0] = 0
    threshold = np.max(arr)
    print(threshold)
    return threshold


def dico_from_bids(list_sub, list_Li, list_anat_Li, list_anat):
    
    dico = {}
    for c, i in enumerate(list_sub):
        dico[i] = []
        dico[i].append(list_Li[c])
    for i in list_anat_Li:
        sub = i.split(os.sep)[-4]
        if sub in dico:
            dico[sub].append(i)
    for i in list_anat:
        sub = i.split(os.sep)[-4]
        if sub in dico:
            dico[sub].append(i)
    for i in dico:
        if len(dico[i]) < 3:
            del dico[i]
    print("number of subjects to preprocess : ", len(dico))     
    return dico


def pipeline_lithium(target_anatLi, target_anat, moving_file_Li,
                     transfo_folder,
                     executable_cat12, standalone_cat12,
                     mcr_matlab, matlabbatch, tpm_file, darteltpm_file,
                     threshold=None):
    print("pipeline launch")
    ''' transformations estimations'''
    # Li to anat Li
    coreg_path_Li = os.path.join(transfo_folder, "Li_to_Lianat.mat")
    if not os.path.exists(coreg_path_Li):
        coregistration(target_anatLi, moving_file_Li, coreg_path_Li)
    print("coreg Li Lianat ok")

    # anat Li to anat
    coreg_path_anat = os.path.join(transfo_folder, "anatLi_to_anat.mat")
    if not os.path.exists(coreg_path_anat):
        coregistration(target_anat, target_anatLi, coreg_path_anat)
    print("coreg anatLi anat ok")

    # NL registration
    deformfiel_nl = target_anat.split(os.sep)
    deformfiel_nl.insert(-1, "mri")
    deformfiel_nl[-1] = "y_{0}".format(deformfiel_nl[-1])
    deformfiel_nl = os.sep.join(deformfiel_nl)

    if not os.path.exists(deformfiel_nl):
        # write matlabbatch
        print("matlabbatch")
        batch_name = os.path.join(transfo_folder, "matlabbatch.m")
        write_matlabbatch(matlabbatch, [target_anat], tpm_file, darteltpm_file,
                          batch_name)
        # anat to MNI
        print("launch car12vbm")
        subprocess.check_call([executable_cat12,
                               "-s", standalone_cat12,
                               "-m", mcr_matlab,
                               "-b", batch_name])
    print("cat12vbm ok")

    '''Transformations applications'''
    trans_Lianat = scipy.io.loadmat(os.path.join(transfo_folder,
                                    "inverse_Li_to_Lianat.mat"))
    trans_Lianat_mat = trans_Lianat['M']
    trans_anat = scipy.io.loadmat(os.path.join(transfo_folder,
                                  "inverse_anatLi_to_anat.mat"))
    trans_anat_mat = trans_anat['M']
    # Linear registrations combinaison
    # mixte_mat = np.dot(trans_anat_mat, trans_Lianat_mat)
    mixte_mat = trans_anat_mat
    # Modify Li affine, linear registration
    li_modified_affine = os.path.join(transfo_folder, "li_modified_affine.nii")
    if not os.path.exists(li_modified_affine):
        img = nibabel.load(moving_file_Li)
        new_affine = np.dot(mixte_mat, img.affine)
        normalized = nibabel.Nifti1Image(img.get_fdata(), new_affine)
        nibabel.save(normalized, li_modified_affine)
    print("Modified Li affine ok")
    # Apply NL deformation on Li
    Li_MNI = os.path.join(transfo_folder, "wli_modified_affine.nii")
    if not os.path.exists(Li_MNI):
        apply_defor(deformfiel_nl, li_modified_affine)
    print("NL deformation applied on 7Li")
    # Apply NL deformation on anat
    anat_MNI_auto = os.path.join(os.path.dirname(target_anat),
                                 "w{0}".format(os.path.basename(target_anat)))
    anat_MNI = os.path.join(transfo_folder,
                            "w{0}".format(os.path.basename(target_anat)))
    if not os.path.exists(anat_MNI):
        apply_defor(deformfiel_nl, target_anat)
        subprocess.check_call(["mv", anat_MNI_auto, anat_MNI])
    print("NL deformation applied on anat 3T")
    # Apply denoising on Li MNI
    name_denoised_Li = os.path.join(transfo_folder,
                                    "sanlm_wli_modified_affine.nii")
    if not os.path.exists(name_denoised_Li):
        denoising_cat12(Li_MNI)
    print("denoising finished : ", name_denoised_Li)
    # MNI space shape checks
    li_img = nibabel.load(name_denoised_Li)
    bg_img = nibabel.load(anat_MNI)
    print("Li MNI shape : ", li_img.get_fdata().shape)
    print("Anat MNI shape : ", bg_img.get_fdata().shape)
    assert li_img.get_fdata().shape ==\
           (121, 145, 121), li_img.get_fdata().shape
    assert li_img.get_fdata().shape ==\
           (121, 145, 121), li_img.get_fdata().shape
    # Threshold
    if not threshold:
        arr = li_img.get_fdata()
        threshold = np.max(arr)*0.25

    ''' Plot results'''
    figname_no_denoi = os.path.join(transfo_folder, "figure_li_anat_MNI")
    fignamedenoi = os.path.join(transfo_folder, "figure_denoised_li_anat_MNI")
    plot_anat_Li(name_denoised_Li, anat_MNI, threshold, fignamedenoi)
    plot_anat_Li(Li_MNI, anat_MNI, threshold, figname_no_denoi)

    return 0


###############################################################################
def main():
    # LAUNCH EXAMPLE
    # inputs
    #     target_anatLi = "/neurospin/psy_sbox/temp_julie/Fawzi/example"\
    #                     "/sub-20181116/Anatomy7T/t1_mpr_tra_iso1_0mm.nii"
    #     target_anat = "/neurospin/psy_sbox/temp_julie/Fawzi/example/"\
    #                 "sub-20181116/Anatomy3T/t1_weighted_sagittal_1_0iso.nii"
    #     moving_file_Li = "/neurospin/psy_sbox/temp_julie/Fawzi/example"\
    #                     "/sub-20181116/Trufi/01-Raw/TRUFI_1000_1.nii"
    #   transfo_folder = "/neurospin/psy_sbox/temp_julie/Fawzi/example/transfo"
    #     # launch
    #     pipeline_lithium(target_anatLi, target_anat, moving_file_Li,
    #                      transfo_folder,
    #                      executable_cat12, standalone_cat12,
    #                      mcr_matlab, matlabbatch, tpm_file, darteltpm_file,
    #                      threshold)
    #  path = "/neurospin/ciclops/projects/BIPLi7/"\
    #         "BIDS/derivatives/Limri_preproc"
    #     liregex = "sub-*/ses-*/lithium/sub-*_ses-*_acq-trufi_limri.nii"
    #     anatliregex = "sub-*/ses-*/anat/sub-*_ses-*_acq-7T_T1w.nii"
    #     anatregex = "sub-*/ses-*/anat/sub-*_ses-*_acq-3T_T1w.nii"

    # PARSER
    parser = argparse.ArgumentParser("Launch 7Li preprocessing")
    # required
    parser.add_argument('--method',
                        choices=["regex", "file_txt", "plot"],
                        help='regex : images inputs from regex'
                             ' or exact filenames\n'
                             'file_txt : images inputs from a text file,'
                             ' one line per subject\n'
                             'plot : to plot an existing preprocessing',
                        required=True,
                        type=str)
    parser.add_argument('--Li',
                        help='if method is regex : regex of the Li images\n'
                             'if method is file_txt : path to the text file'
                             'containing paths of Li images',
                        required=True,
                        type=str)
    parser.add_argument('--Lianat',
                        help='if method is regex : regex of the anat images'
                             ' acquired with Li coil\n'
                             'if method is file_txt : path to the text file'
                             'containing paths of anat images'
                             ' acquired with Li coil',
                        required=True,
                        type=str)
    parser.add_argument('--anat',
                        help='if method is regex : regex of the anat images'
                             ' acquired with H coil\n'
                             'if method is file_txt : path to the text file'
                             'containing paths of anat images'
                             ' acquired with H coil',
                        required=True,
                        type=str)
    parser.add_argument('--launch',
                        help='cluster : launch on alambic\n'
                             'local : launch on your computer',
                        choices=["cluster", "local"],
                        required=True,
                        type=str)
    
    # optionnal
    parser.add_argument('--Li_file_txt',
                        help='Path to a text file containing the paths'
                             ' (one line per image path) of the Li mri.',
                        type=str)
    parser.add_argument('--Lianat_file_txt',
                        help='Path to a text file containing the paths'
                             ' (one line per image path) of the anat mri'
                             ' with an Li coil.',
                        type=str)
    parser.add_argument('--anat_file_txt',
                        help='Path to a text file containing the paths'
                             ' (one line per image path) of the anat mri.'
                             ' with an H coil',
                        type=str)
    parser.add_argument('--executable_cat12',
                        help='Path to the executable_cat12.\n'
                             'example : [...]/cat12-standalone/'
                             'standalone/cat_standalone.sh',
                        default='/i2bm/local/cat12-standalone/'
                                'standalone/cat_standalone.sh',
                        type=str)
    parser.add_argument('--standalone_cat12',
                        help='cat12 standalone folder.\n'
                             'example : [...]/cat12-standalone',
                        default='/i2bm/local/cat12-standalone',
                        type=str,)
    parser.add_argument('--mcr_matlab',
                        help='Path to the mcr_matlab.\n'
                             'example : [...]/cat12-standalone/mcr/v93',
                        default='/i2bm/local/cat12-standalone/mcr/v93',
                        type=str)
    parser.add_argument('--tpm_file',
                        help='Path to the tpm_file cat12vbm file.\n'
                             'example : cat12-standalone/spm12_mcr/'
                             'home/gaser/gaser/spm/spm12/tpm/TPM.nii',
                        default='/i2bm/local/cat12-standalone/spm12_mcr/'
                                'home/gaser/gaser/spm/spm12/tpm/TPM.nii',
                        type=str)
    parser.add_argument('--darteltpm_file',
                        help='Path to the dartel tpm file.\n'
                             'example : [...]/cat12-standalone/spm12_mcr/'
                             'home/gaser/gaser/spm/spm12/toolbox/cat12/'
                             'templates_volumes/Template_1_IXI555_MNI152.nii',
                        default='/i2bm/local/cat12-standalone/spm12_mcr/'
                                'home/gaser/gaser/spm/spm12/toolbox/cat12/tem'
                                'plates_volumes/Template_1_IXI555_MNI152.nii',
                        type=str)
    parser.add_argument('--threshold',
                        help="threshold",
                        default=None,
                        type=int)
    options = parser.parse_args()
    # required
    method = options.method
    Li = options.Li
    Lianat = options.Lianat
    anat = options.anat
    # initialization
    matlab_cmd = options.matlab_cmd
    spm.SPMCommand.set_mlab_paths(matlab_cmd=matlab_cmd, use_mcr=True)
    print("spm mcr version", spm.SPMCommand().version)
    # standalone cat12vbm matlab config
    executable_cat12 = options.executable_cat12
    standalone_cat12 = options.standalone_cat12
    mcr_matlab = options.mcr_matlab
    # templates
    tpm_file = options.tpm_file
    darteltpm_file = options.darteltpm_file
    # resources dir
    resource_dir = os.path.join(
            os.path.dirname(rlink_7Limri_2022.__file__), "resources")
    matlabbatch = os.path.join(resource_dir, "cat12vbm_matlabbatch.m")

    # options
    # method : one or list
    method = options.method
    # launch: local or cluster
    launch = options.launch
    # use a file.txt
    Li_file_txt = options.Li_file_txt
    Lianat_file_txt = options.Lianat_file_txt
    anat_file_txt = options.anat_file_txt
    # threshold 20 or 200
    threshold = options.threshold

    # method choice
    if method == "file_txt":
        # check if there is all required files
        if options.Li_file_txt and (options.Lianat_file_txt is None
                                    or options.anat_file_txt is None):
            parser.error("--options.Li_file_txt requires"
                         " --options.Lianat_file_txt and"
                         " --options.anat_file_txt.")
        elif options.Lianat_file_txt and (options.Li_file_txt is None
                                        or options.anat_file_txt is None):
            parser.error("--options.Lianat_file_txt requires"
                         " --options.Li_file_txt and"
                         " --options.anat_file_txt.")
        elif options.anat_file_txt and (options.Lianat_file_txt is None
                                        or options.Li_file_txt is None):
            parser.error("--options.anat_file_txt requires"
                         " --options.Lianat_file_txt and"
                         " --options.Li_file_txt.")
        list_Li = get_list_from_txt(Li_file_txt)
        list_Lianat = get_list_from_txt(Lianat_file_txt)
        list_anat = get_list_from_txt(anat_file_txt)
        sub_Li = [os.path.basename(path).split("_")[0] for path in list_Li]
        sub_Lianat = [os.path.basename(path).split("_")[0] for path
                      in list_Lianat]
        sub_anat = [os.path.basename(path).split("_")[0] for path in list_anat]
        assert len(list_Li) == len(list_Lianat) == len(list_anat),\
                                                      (len(list_Li),
                                                       len(list_Lianat),
                                                       len(list_anat))
        assert sub_Li == sub_Lianat == sub_anat, "each text file must"\
                                                 " have the same order"

    elif method == "regex":
        # modify here too
        list_Li = glob.glob(Li)
        list_Lianat = glob.glob(Lianat)
        list_anat = glob.glob(anat)
        assert len(list_Li) != 0, list_Li
        assert len(list_Lianat) != 0, list_Lianat
        assert len(list_anat) != 0, list_anat
        list_sub = [os.path.basename(path).split("_")[0] for path in list_Li]
        dico = dico_from_bids(list_sub, list_Li, list_Lianat, list_anat)
   
    elif method == "plot":
        # works only with one subject
        output = transfo_folder = os.path.dirname(Li)
        li_mni_denoised = os.path.join(output,
                                       "sanlm_wli_modified_affine.nii")
        figname = os.path.join(output, "snap_Li_preproc_results")
        anat_MNI = anat.split(os.sep)
        anat_MNI[-1] = "w{0}".format(anat_MNI[-1])
        anat_MNI = os.sep.join(anat_MNI)
        if not threshold:
            li_img = nibabel.load(li_mni_denoised)
            arr = li_img.get_fdata()
            threshold = np.max(arr)*0.25
        plot_anat_Li(li_mni_denoised, anat_MNI, threshold, figname)

    # launch choice
    if launch == "local":
        for i in dico:
            print("launch {0}".format(i))
            transfo_folder = os.path.join(os.path.dirname(dico[i][0]),
                                          "transfo")
            os.makedirs(transfo_folder)
            target_Lianat = dico[i][1]
            target_anat = dico[i][2]
            moving_file_Li = dico[i][0]
            pipeline_lithium(target_Lianat, target_anat, moving_file_Li,
                             transfo_folder,
                             executable_cat12, standalone_cat12,
                             mcr_matlab, matlabbatch, tpm_file,
                             darteltpm_file)
    elif launch == "cluster":
        for c, i in enumerate(dico):
            prefix_PBS = "/neurospin/psy_sbox/temp_julie/Fawzi/"\
                            "jobs_alambic/job_{0}".format(dico[i][0])
            transfo_folder = os.path.join(os.path.dirname(dico[i][0]),
                                          "transfo")
            os.makedirs(transfo_folder)
            target_Lianat = dico[i][1]
            target_anat = dico[i][2]
            moving_file_Li = dico[i][0]
            PBS_filename = createPBS(prefix_PBS, c, target_Lianat, target_anat,
                                     moving_file_Li,
                                     transfo_folder,
                                     executable_cat12, standalone_cat12,
                                     mcr_matlab, matlabbatch, tpm_file,
                                     darteltpm_file)
            launch_cluster(PBS_filename) 

    

if __name__ == "__main__":
    main()
