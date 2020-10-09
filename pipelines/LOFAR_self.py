#!/usr/bin/env python
# -*- coding: utf-8 -*-

# perform self-calibration on a group of SBs concatenated in TCs.
# they need to be in "./mss/"

import sys, os, glob, re
import lsmtool
import numpy as np

# Survey
#if 'LBAsurvey' in os.getcwd():
#    obs = os.getcwd().split('/')[-1]
#    if not os.path.exists('mss'):
#        os.makedirs('mss')
#        for i, tc in enumerate(glob.glob('/home/fdg/data/LBAsurvey/c*-o*/%s/mss/*' % obs)):
#            tc_ren = 'TC%02i.MS' % i
#            print('cp -r %s mss/%s' % (tc,tc_ren))
#            os.system('cp -r %s mss/%s' % (tc,tc_ren))

########################################################
from LiLF import lib_ms, lib_img, lib_util, lib_log
logger_obj = lib_log.Logger('pipeline-self.logger')
logger = lib_log.logger
s = lib_util.Scheduler(log_dir = logger_obj.log_dir, dry = False)
w = lib_util.Walker('pipeline-self.walker')

parset = lib_util.getParset()
parset_dir = parset.get('LOFAR_self','parset_dir')
subtract_outside = parset.getboolean('LOFAR_self','subtract_outside')
sourcedb = parset.get('model','sourcedb')
apparent = parset.getboolean('model','apparent')
userReg = parset.get('model','userReg')

#############################################################################
# Clear
if w.todo('cleaning'):
    logger.info('Cleaning...')
    lib_util.check_rm('img')
    os.makedirs('img')

    # here images, models, solutions for each group will be saved
    lib_util.check_rm('self')
    if not os.path.exists('self/images'): os.makedirs('self/images')
    if not os.path.exists('self/solutions'): os.makedirs('self/solutions')
    if not os.path.exists('self/plots'): os.makedirs('self/plots')

    w.done('cleaning')
### DONE

MSs = lib_ms.AllMSs( glob.glob('mss/TC*[0-9].MS'), s )
try:
    MSs.print_HAcov()
except:
    logger.error('Problem with HAcov, continue anyway.')

# make beam to the first mid null
phasecentre = MSs[0].getPhaseCentre()
MSs[0].makeBeamReg('self/beam.reg', freq='mid', to_null=True)
beamReg = 'self/beam.reg'

# set image pixelsize
pixscale = (2/3)*MSs[0].getResolution() if MSs.isLBA else MSs[0].getResolution()
# set image size
imgsizepix = int(2.1*MSs[0].getFWHM(freq='mid')*3600/pixscale)
if imgsizepix%2 != 0: imgsizepix += 1 # prevent odd img sizes
# for frequency scaling
freqscale = np.mean(MSs.getFreqs())/58.e6
# find smooth factor
smoothfactor = 0.002 if  MSs.isHBA else 0.01

#################################################################
# Get online model
if sourcedb == '':
    if not os.path.exists('tgts.skydb'):
        fwhm = MSs[0].getFWHM(freq='min')
        radeg = phasecentre[0]
        decdeg = phasecentre[1]
        # get model the size of the image (radius=fwhm/2)
        os.system('wget -O tgts.skymodel "https://lcs165.lofar.eu/cgi-bin/gsmv1.cgi?coord=%f,%f&radius=%f&unit=deg"' % (radeg, decdeg, fwhm/2.)) # ASTRON
        lsm = lsmtool.load('tgts.skymodel')#, beamMS=MSs.getListObj()[0])
        lsm.remove('I<1')
        lsm.write('tgts.skymodel', clobber=True)
        os.system('makesourcedb outtype="blob" format="<" in=tgts.skymodel out=tgts.skydb')
        apparent = False

    sourcedb = 'tgts.skydb'

#################################################################################################
# Add model to MODEL_DATA
# copy sourcedb into each MS to prevent concurrent access from multiprocessing to the sourcedb
sourcedb_basename = sourcedb.split('/')[-1]
for MS in MSs.getListStr():
    lib_util.check_rm(MS+'/'+sourcedb_basename)
    logger.debug('Copy: '+sourcedb+' -> '+MS)
    os.system('cp -r '+sourcedb+' '+MS)

if w.todo('init_model'):

    # note: do not add MODEL_DATA or the beam is transported from DATA, while we want it without beam applied
    logger.info('Creating CORRECTED_DATA...')
    MSs.run('addcol2ms.py -m $pathMS -c CORRECTED_DATA -i DATA', log='$nameMS_addcol.log', commandType='python')
    
    logger.info('Add model to MODEL_DATA...')
    if apparent:
        MSs.run('DPPP '+parset_dir+'/DPPP-predict.parset msin=$pathMS pre.usebeammodel=false pre.sourcedb=$pathMS/'+sourcedb_basename, log='$nameMS_pre.log', commandType='DPPP')
    else:
        MSs.run('DPPP '+parset_dir+'/DPPP-predict.parset msin=$pathMS pre.usebeammodel=true pre.sourcedb=$pathMS/'+sourcedb_basename, log='$nameMS_pre.log', commandType='DPPP')

    w.done('init_model')
### DONE

#####################################################################################################
# Self-cal cycle
for c in range(2):

    logger.info('Start selfcal cycle: '+str(c))

    if c == 0:
        if w.todo('set_corrected_data'):
            logger.info('Set CORRECTED_DATA = DATA...')
            MSs.run('taql "update $pathMS set CORRECTED_DATA = DATA"', log='$nameMS_taql-c'+str(c)+'.log', commandType='general')
            w.done('set_corrected_data')
        ### DONE
    else:

        if w.todo('init_apply_c%02i' % c):
            # correct G - group*_TC.MS:CORRECTED_DATA -> group*_TC.MS:CORRECTED_DATA
            logger.info('Correcting G...')
            MSs.run('DPPP '+parset_dir+'/DPPP-cor.parset msin=$pathMS msin.datacolumn=CORRECTED_DATA cor.parmdb=self/solutions/cal-g2-c0.h5 cor.correction=amplitudeSmooth', \
                    log='$nameMS_corG-c'+str(c)+'.log', commandType='DPPP')

            # correct FR - group*_TC.MS:CORRECTED_DATA -> group*_TC.MS:CORRECTED_DATA
            logger.info('Correcting FR...')
            MSs.run('DPPP '+parset_dir+'/DPPP-cor.parset msin=$pathMS msin.datacolumn=CORRECTED_DATA cor.parmdb=self/solutions/cal-g1-c0.h5 cor.correction=rotationmeasure000', \
                    log='$nameMS_corFR-c'+str(c)+'.log', commandType='DPPP')

            w.done('init_apply_c%02i' % c)
        ### DONE

    if w.todo('solve_tec1_c%02i' % c):

        # Smooth CORRECTED_DATA -> SMOOTHED_DATA
        logger.info('BL-based smoothing...')
        MSs.run('BLsmooth.py -c 8 -n 8 -f {} -r -i CORRECTED_DATA -o SMOOTHED_DATA $pathMS'.format(smoothfactor), log='$nameMS_smooth-c'+str(c)+'.log', commandType='python')
        MSs.run('BLsmooth.py -c 8 -n 8 -f {} -r -i MODEL_DATA -o MODEL_DATA $pathMS'.format(smoothfactor), log='$nameMS_smooth-c'+str(c)+'.log', commandType='python')

        # solve TEC - ms:SMOOTHED_DATA
        logger.info('Solving TEC1...')
        if MSs.isLBA:
            MSs.run('DPPP '+parset_dir+'/DPPP-solTEC.parset msin=$pathMS sol.h5parm=$pathMS/tec1.h5 \
                    msin.baseline="[CR]*&&;!RS208*;!RS210*;!RS307*;!RS310*;!RS406*;!RS407*;!RS409*;!RS508*;!RS509*" \
                    sol.antennaconstraint=[[CS002LBA,CS003LBA,CS004LBA,CS005LBA,CS006LBA,CS007LBA]] \
                    sol.uvlambdamin = {} sol.uvmmax = {} sol.solint=15 sol.nchan=8'.format(100*freqscale,80e3*freqscale),
                    log='$nameMS_solTEC-c'+str(c)+'.log', commandType='DPPP')
            lib_util.run_losoto(s, 'tec1-c' + str(c), [ms + '/tec1.h5' for ms in MSs.getListStr()],
                                        [parset_dir + '/losoto-resetremote-lba.parset', parset_dir + '/losoto-plot-tec.parset'])
        elif MSs.isHBA:
            MSs.run('DPPP ' + parset_dir + '/DPPP-solTEC.parset msin=$pathMS sol.h5parm=$pathMS/tec1.h5 \
                    msin.baseline="[CR]*&&;!RS208*;!RS210*;!RS307*;!RS310*;!RS406*;!RS407*;!RS409*;!RS508*;!RS509*" \
                    sol.antennaconstraint=[[CS002HBA0,CS002HBA1,CS003HBA0,CS003HBA1,CS004HBA0,CS004HBA1,CS005HBA0,CS005HBA1,CS006HBA0,CS006HBA1,CS007HBA0,CS007HBA1]] \
                    sol.uvlambdamin = {} sol.uvmmax = {} sol.solint=15 sol.nchan=8'.format(100*freqscale, 80e3*freqscale), \
                    log='$nameMS_solTEC-c' + str(c) + '.log', commandType='DPPP')
            lib_util.run_losoto(s, 'tec1-c' + str(c), [ms + '/tec1.h5' for ms in MSs.getListStr()],
                                [parset_dir + '/losoto-resetremote-hba.parset', parset_dir + '/losoto-plot-tec.parset'])
        os.system('mv cal-tec1-c' + str(c) + '.h5 self/solutions/')
        os.system('mv plots-tec1-c' + str(c) + ' self/plots/')

        w.done('solve_tec1_c%02i' % c)
    ### DONE
    
    if w.todo('cor_tec1_c%02i' % c):
        # correct TEC - group*_TC.MS:CORRECTED_DATA -> group*_TC.MS:CORRECTED_DATA
        logger.info('Correcting TEC1...')
        MSs.run('DPPP '+parset_dir+'/DPPP-cor.parset msin=$pathMS msin.datacolumn=CORRECTED_DATA\
                cor.parmdb=self/solutions/cal-tec1-c'+str(c)+'.h5 cor.correction=tec000', \
                log='$nameMS_corTEC-c'+str(c)+'.log', commandType='DPPP')

        w.done('cor_tec1_c%02i' % c)
    ### DONE

    if w.todo('solve_tec2_c%02i' % c):
        # Smooth CORRECTED_DATA -> SMOOTHED_DATA
        logger.info('BL-based smoothing...')
        MSs.run('BLsmooth.py -c 8 -n 8 -f {} -r -i CORRECTED_DATA -o SMOOTHED_DATA $pathMS'.format(smoothfactor), log='$nameMS_smooth-c'+str(c)+'.log', commandType='python')
        MSs.run('BLsmooth.py -c 8 -n 8 -f {} -r -i MODEL_DATA -o MODEL_DATA $pathMS'.format(smoothfactor), log='$nameMS_smooth-c'+str(c)+'.log', commandType='python')

        # solve TEC - ms:SMOOTHED_DATA
        logger.info('Solving TEC2...')
        if MSs.isLBA:
            MSs.run('DPPP '+parset_dir+'/DPPP-solTEC.parset msin=$pathMS sol.h5parm=$pathMS/tec2.h5 \
                    sol.antennaconstraint=[[CS001LBA,CS002LBA,CS003LBA,CS004LBA,CS005LBA,CS006LBA,CS007LBA,CS011LBA,CS013LBA,CS017LBA,'
                                       'CS021LBA,CS024LBA,CS026LBA,CS027LBA,CS028LBA,CS030LBA,CS031LBA,CS032LBA,CS101LBA,CS103LBA,CS201LBA,'
                                       'CS301LBA,CS302LBA,CS401LBA,CS501LBA,RS106LBA,RS205LBA,RS305LBA,RS306LBA,RS503LBA]] \
                    sol.solint=1 sol.nchan=4 sol.uvlambdamin = {} sol.uvmmax = {} sol.solint=15 sol.nchan=8'.format(
                    100*freqscale, 80e3*freqscale), log='$nameMS_solTEC-c'+str(c)+'.log', commandType='DPPP')
    
        elif MSs.isHBA:
            MSs.run('DPPP ' + parset_dir + '/DPPP-solTEC.parset msin=$pathMS sol.h5parm=$pathMS/tec2.h5 \
                    sol.antennaconstraint=[[CS002HBA0,CS002HBA1,'
                                           'CS003HBA0,CS003HBA1,'
                                           'CS004HBA0,CS004HBA1,'
                                           'CS005HBA0,CS005HBA1,'
                                           'CS006HBA0,CS006HBA1,'
                                           'CS007HBA0,CS007HBA1,'
                                           'CS011HBA0,CS011HBA1,'
                                           'CS013HBA0,CS013HBA1,'
                                           'CS017HBA0,CS017HBA1,'
                                           'CS021HBA0,CS021HBA1,'
                                           'CS024HBA0,CS024HBA1,'
                                           'CS026HBA0,CS026HBA1,'
                                           'CS028HBA0,CS028HBA1,'
                                           'CS030HBA0,CS030HBA1,'
                                           'CS031HBA0,CS031HBA1,'
                                           'CS032HBA0,CS032HBA1,'
                                           'CS101HBA0,CS101HBA1,'
                                           'CS103HBA0,CS103HBA1,'
                                           'CS201HBA0,CS201HBA1,'
                                           'CS301HBA0,CS301HBA1,'
                                           'CS302HBA0,CS302HBA1,'
                                           'CS401HBA0,CS401HBA1,'
                                           'CS501HBA0,CS501HBA1,'
                                           'RS106HBA,'
                                           'RS205HBA,'
                                           'RS305HBA,'
                                           'RS306HBA,'
                                           'RS503HBA]] \
                    sol.solint=1 sol.nchan=4 sol.uvlambdamin = {} sol.uvmmax = {}'.format(
                    100*freqscale, 80e3*freqscale), log='$nameMS_solTEC-c' + str(c) + '.log', commandType='DPPP')

        lib_util.run_losoto(s, 'tec2-c' + str(c), [ms + '/tec2.h5' for ms in MSs.getListStr()],
                                [parset_dir + '/losoto-plot-tec.parset'])
        os.system('mv cal-tec2-c'+str(c)+'.h5 self/solutions/')
        os.system('mv plots-tec2-c'+str(c)+' self/plots/')

        w.done('solve_tec2_c%02i' % c)
    ### DONE

    if w.todo('cor_tec2_c%02i' % c):
        # correct TEC - group*_TC.MS:CORRECTED_DATA -> group*_TC.MS:CORRECTED_DATA
        logger.info('Correcting TEC2...')
        MSs.run('DPPP '+parset_dir+'/DPPP-cor.parset msin=$pathMS msin.datacolumn=CORRECTED_DATA\
                cor.parmdb=self/solutions/cal-tec2-c'+str(c)+'.h5 cor.correction=tec000', \
                log='$nameMS_corTEC-c'+str(c)+'.log', commandType='DPPP')

        w.done('cor_tec2_c%02i' % c)
    ### DONE

    # AMP+FR DIE correction
    if c == 0:

        if w.todo('solve_fr_c%02i' % c):
            # Convert to circular CORRECTED_DATA -> CORRECTED_DATA
            logger.info('Converting to circular...')
            MSs.run('mslin2circ.py -i $pathMS:CORRECTED_DATA -o $pathMS:CORRECTED_DATA', log='$nameMS_circ2lin.log', commandType='python', maxThreads=2)
    
            # DIE Calibration - ms:CORRECTED_DATA
            logger.info('Solving slow G1...')
            if MSs.isLBA:
                MSs.run('DPPP '+parset_dir+'/DPPP-solG.parset msin=$pathMS sol.h5parm=$pathMS/g1.h5' \
                    'sol.antennaconstraint = [[CS001LBA, CS002LBA, CS003LBA, CS004LBA, CS005LBA, CS006LBA, CS007LBA,'
                                              'CS011LBA, CS013LBA, CS017LBA, CS021LBA, CS024LBA, CS026LBA, CS028LBA,'
                                              'CS030LBA, CS031LBA, CS032LBA, CS101LBA, CS103LBA, CS201LBA, CS301LBA,'
                                              'CS302LBA, CS401LBA, CS501LBA]] sol.uvlambdamin = {} sol.uvmmax = {}'.format(
                    100*freqscale, 80e3*freqscale), log='$nameMS_solG1-c'+str(c)+'.log', commandType='DPPP')
            elif MSs.isHBA:
                MSs.run('DPPP ' + parset_dir + '/DPPP-solG.parset msin=$pathMS sol.h5parm=$pathMS/g1.h5 \
                        sol.antennaconstraint=[[CS002HBA0,CS002HBA1,'
                                               'CS003HBA0,CS003HBA1,'
                                               'CS004HBA0,CS004HBA1,'
                                               'CS005HBA0,CS005HBA1,'
                                               'CS006HBA0,CS006HBA1,'
                                               'CS007HBA0,CS007HBA1,'
                                               'CS011HBA0,CS011HBA1,'
                                               'CS013HBA0,CS013HBA1,'
                                               'CS017HBA0,CS017HBA1,'
                                               'CS021HBA0,CS021HBA1,'
                                               'CS024HBA0,CS024HBA1,'
                                               'CS026HBA0,CS026HBA1,'
                                               'CS028HBA0,CS028HBA1,'
                                               'CS030HBA0,CS030HBA1,'
                                               'CS031HBA0,CS031HBA1,'
                                               'CS032HBA0,CS032HBA1,'
                                               'CS101HBA0,CS101HBA1,'
                                               'CS103HBA0,CS103HBA1,'
                                               'CS201HBA0,CS201HBA1,'
                                               'CS301HBA0,CS301HBA1,'
                                               'CS302HBA0,CS302HBA1,'
                                               'CS401HBA0,CS401HBA1,'
                                               'CS501HBA0,CS501HBA1]] sol.uvlambdamin = {} sol.uvmmax = {}'.format(
                        100*freqscale, 80e3*freqscale),
                        log = '$nameMS_solG1-c' + str(c) + '.log', commandType = 'DPPP')
            lib_util.run_losoto(s, 'g1-c'+str(c), [MS+'/g1.h5' for MS in MSs.getListStr()], \
                [parset_dir+'/losoto-plot-amp.parset', parset_dir+'/losoto-plot-ph.parset', parset_dir+'/losoto-fr.parset'])
            os.system('mv plots-g1-c'+str(c)+' self/plots/')
            os.system('mv cal-g1-c'+str(c)+'.h5 self/solutions/')
    
            # Convert back to linear CORRECTED_DATA -> CORRECTED_DATA
            logger.info('Converting to linear...')
            MSs.run('mslin2circ.py -r -i $pathMS:CORRECTED_DATA -o $pathMS:CORRECTED_DATA', log='$nameMS_circ2lin.log', commandType='python', maxThreads=2)

            w.done('solve_fr_c%02i' % c)
        ### DONE

        if w.todo('cor_fr_c%02i' % c):
            # Correct FR - group*_TC.MS:CORRECTED_DATA -> group*_TC.MS:CORRECTED_DATA
            logger.info('Correcting FR...')
            MSs.run('DPPP '+parset_dir+'/DPPP-cor.parset msin=$pathMS msin.datacolumn=CORRECTED_DATA \
                    cor.parmdb=self/solutions/cal-g1-c'+str(c)+'.h5 cor.correction=rotationmeasure000', \
                    log='$nameMS_corFR-c'+str(c)+'.log', commandType='DPPP')
    
            w.done('cor_fr_c%02i' % c)
        ### DONE

        if w.todo('solve_g_c%02i' % c):
            # DIE Calibration - ms:CORRECTED_DATA
            logger.info('Solving slow G2...')
            MSs.run('DPPP '+parset_dir+'/DPPP-solG.parset msin=$pathMS sol.h5parm=$pathMS/g2.h5 sol.uvlambdamin = {}'
                                       ' sol.uvmmax = {}'.format(100*freqscale, 80e3*freqscale), \
                    log='$nameMS_solG2-c'+str(c)+'.log', commandType='DPPP')
            lib_util.run_losoto(s, 'g2-c'+str(c), [MS+'/g2.h5' for MS in MSs.getListStr()], \
                    [parset_dir+'/losoto-plot-amp.parset', parset_dir+'/losoto-plot-ph.parset', parset_dir+'/losoto-amp.parset'])
            os.system('mv plots-g2-c'+str(c)+' self/plots/')
            os.system('mv cal-g2-c'+str(c)+'.h5 self/solutions/')

            w.done('solve_g_c%02i' % c)
        ### DONE

        if w.todo('cor_g_c%02i' % c):
            # correct G - group*_TC.MS:CORRECTED_DATA -> group*_TC.MS:CORRECTED_DATA
            logger.info('Correcting G...')
            MSs.run('DPPP '+parset_dir+'/DPPP-cor.parset msin=$pathMS msin.datacolumn=CORRECTED_DATA \
                    cor.parmdb=self/solutions/cal-g2-c'+str(c)+'.h5 cor.correction=amplitudeSmooth', \
                    log='$nameMS_corG-c'+str(c)+'.log', commandType='DPPP')

            w.done('cor_g_c%02i' % c)
        ### DONE

    ###################################################################################################################
    # clen on concat.MS:CORRECTED_DATA

    imagename = 'img/wideM-'+str(c)
    if w.todo('imaging_c%02i' % c):
        # baseline averaging possible as we cut longest baselines (also it is in time, where smearing is less problematic)
        logger.info('Cleaning (cycle: ' + str(c) + ')...')
        if c == 0:
            kwargs = {'do_predict': True, 'baseline_averaging': '', 'parallel_gridding': 2, 'auto_mask': 2.5}
        else:
            kwargs = {'baseline_averaging': '', 'parallel_gridding': 2, 'auto_mask': 2.0, 'fits_mask':maskname}
        if MSs.isLBA:
            kwargs.update({'minuv_l': 30, 'maxuv_l': 4500, 'parallel_deconvolution': 512, 'parallel_gridding': 2, 'channels_out': MSs.getChout(4.e6)})
        elif MSs.isHBA:
            kwargs.update({'minuv_l': 80, 'maxuv_l': 12000, 'parallel_gridding': 1, 'channels_out': MSs.getChout(8.e6)}) # 'parallel_deconvolution': 2024,

        lib_util.run_wsclean(s, 'wsclean-c' + str(c) + '.log', MSs.getStrWsclean(), name=imagename, save_source_list='',
                         size=imgsizepix, scale='{}arcsec'.format(pixscale), \
                         weight='briggs -0.3', niter=1000000, no_update_model_required='', mgain=0.85, \
                         local_rms='', auto_threshold=1.5, multiscale='', multiscale_scale_bias=0.6, \
                         join_channels='', fit_spectral_pol=3, deconvolution_channels=3, **kwargs)

        os.system('cat logs/wsclean-c' + str(c) + '.log | grep "background noise"')

        w.done('imaging_c%02i' % c)
    ### DONE

    if c == 0 and subtract_outside:

        if w.todo('lowres_setdata_c%02i' % c):
            # Subtract model from all TCs - ms:CORRECTED_DATA - MODEL_DATA -> ms:CORRECTED_DATA (selfcal corrected, beam corrected, high-res model subtracted)
            logger.info('Subtracting high-res model (CORRECTED_DATA = CORRECTED_DATA - MODEL_DATA)...')
            MSs.run('taql "update $pathMS set CORRECTED_DATA = CORRECTED_DATA - MODEL_DATA"', log='$nameMS_taql-c'+str(c)+'.log', commandType='general')

            w.done('lowres_setdata_c%02i' % c)
        ### DONE

        if w.todo('lowres_imaging_c%02i' % c):
            # Making beam mask
            lib_util.run_wsclean(s, 'wscleanLRmask.log', MSs.getStrWsclean(), name='img/tmp', size=imgsizepix,
                                 scale='{}arcsec'.format(3*pixscale))
            os.system('mv img/tmp-image.fits img/wide-lr-mask.fits')
            lib_img.blank_image_reg('img/wide-lr-mask.fits', beamReg, blankval=0.)
            lib_img.blank_image_reg('img/wide-lr-mask.fits', beamReg, blankval=1., inverse=True)

            # reclean low-resolution
            logger.info('Cleaning low resolution...')
            imagename_lr = 'img/wide-lr'
            if MSs.isLBA:
                kwargs = {'minuv_l': 30, 'parallel_deconvolution': 512, 'channels_out': MSs.getChout(2.e6), 'parallel_gridding': 4}
            elif MSs.isHBA:
                kwargs = {'minuv_l': 80,  'parallel_deconvolution': 2024, 'channels_out': MSs.getChout(4.e6), 'parallel_gridding': 2}
            lib_util.run_wsclean(s, 'wscleanLR.log', MSs.getStrWsclean(), name=imagename_lr, do_predict=True, \
                                 temp_dir='./', size=imgsizepix, scale='{}arcsec'.format(3*pixscale), \
                                 weight='briggs -1', niter=50000, no_update_model_required='',
                                 maxuvw_m=6000, taper_gaussian='{}arcsec'.format(20*pixscale), mgain=0.85, \
                                 baseline_averaging='', local_rms='', auto_mask=3,
                                 auto_threshold=1.5, fits_mask='img/wide-lr-mask.fits', \
                                 join_channels='', fit_spectral_pol=5,
                                 deconvolution_channels=5, **kwargs)
            w.done('lowres_imaging_c%02i' % c)
        ### DONE

        if w.todo('lowres_flag_c%02i' % c):
            ##############################################
            # Flag on empty dataset

            # Subtract low-res model - CORRECTED_DATA = CORRECTED_DATA - MODEL_DATA
            logger.info('Subtracting low-res model (CORRECTED_DATA = CORRECTED_DATA - MODEL_DATA)...')
            MSs.run('taql "update $pathMS set CORRECTED_DATA = CORRECTED_DATA - MODEL_DATA"',
                    log='$nameMS_taql-c' + str(c) + '.log', commandType='general')

            # Flag on residuals (CORRECTED_DATA)
            # What to do for HBA?
            if MSs.isLBA:
                logger.info('Flagging residuals...')
                MSs.run('DPPP ' + parset_dir + '/DPPP-flag.parset msin=$pathMS', log='$nameMS_flag-c' + str(c) + '.log',
                        commandType='DPPP')
                w.done('lowres_flag_c%02i' % c)
        ### DONE

        if w.todo('lowres_corrupt_c%02i' % c):
            ##############################################
            # Prepare SUBTRACTED_DATA

            # corrupt model with TEC+FR+Beam2ord solutions - ms:MODEL_DATA -> ms:MODEL_DATA
            logger.info('Corrupt low-res model: TEC1...')
            MSs.run('DPPP ' + parset_dir + '/DPPP-cor.parset msin=$pathMS msin.datacolumn=MODEL_DATA msout.datacolumn=MODEL_DATA  \
                    cor.parmdb=self/solutions/cal-tec1-c' + str(c) + '.h5 cor.correction=tec000 cor.invert=False', \
                    log='$nameMS_corrupt.log', commandType='DPPP')
            logger.info('Corrupt low-res model: TEC2...')
            MSs.run('DPPP ' + parset_dir + '/DPPP-cor.parset msin=$pathMS msin.datacolumn=MODEL_DATA msout.datacolumn=MODEL_DATA  \
                    cor.parmdb=self/solutions/cal-tec2-c' + str(c) + '.h5 cor.correction=tec000 cor.invert=False', \
                    log='$nameMS_corrupt.log', commandType='DPPP')
            MSs.run('DPPP ' + parset_dir + '/DPPP-cor.parset msin=$pathMS msin.datacolumn=MODEL_DATA msout.datacolumn=MODEL_DATA \
                    cor.parmdb=self/solutions/cal-g1-c' + str(
                c) + '.h5 cor.correction=rotationmeasure000 cor.invert=False', \
                    log='$nameMS_corrupt.log', commandType='DPPP')
            logger.info('Corrupt low-res model: G...')
            MSs.run('DPPP ' + parset_dir + '/DPPP-cor.parset msin=$pathMS msin.datacolumn=MODEL_DATA msout.datacolumn=MODEL_DATA \
                    cor.parmdb=self/solutions/cal-g2-c' + str(
                c) + '.h5 cor.correction=amplitudeSmooth cor.invert=False', \
                    log='$nameMS_corrupt.log', commandType='DPPP')

            w.done('lowres_corrupt_c%02i' % c)
        ### DONE

        if w.todo('lowres_subtract_c%02i' % c):
            # Subtract low-res model - CORRECTED_DATA = DATA - MODEL_DATA
            logger.info('Subtracting low-res model (CORRECTED_DATA = DATA - MODEL_DATA)...')
            MSs.run('taql "update $pathMS set CORRECTED_DATA = DATA - MODEL_DATA"',
                    log='$nameMS_taql-c' + str(c) + '.log', commandType='general')

            w.done('lowres_subtract_c%02i' % c)
        ### DONE
    if c == 0:
        # make a mask for next cycle
        im = lib_img.Image(imagename + '-MFS-image.fits' )
        im.makeMask(threshisl=5)
        maskname = imagename + '-mask.fits'

        if w.todo('lowres_predict_c%02i' % c):
            # Recreate MODEL_DATA
            logger.info('Predict model...')
            chanout = str(MSs.getChout(8.e6) if MSs.isHBA else MSs.getChout(4.e6))
            s.add(
                'wsclean -predict -name img/wideM-' + str(c) + ' -j ' + str(s.max_processors) + ' -channels-out '+chanout+' '
                + MSs.getStrWsclean(), \
                log='wscleanPRE-c' + str(c) + '.log', commandType='wsclean', processors='max')
            s.run(check=True)

            w.done('lowres_predict_c%02i' % c)
            #DONE

# Copy images
[os.system('mv img/wideM-' + str(c) + '-MFS-image*.fits self/images') for c in range(2)]
[os.system('mv img/wideM-' + str(c) + '-MFS-residual*.fits self/images') for c in range(2)]
[os.system('mv img/wideM-' + str(c) + '-sources*.txt self/images') for c in range(2)]
os.system('mv logs self')
# os.system('makepb.py -o img/avgbeam.fits -i img/wideM-1')
# os.system('mv img/avgbeam.fits self/images')

logger.info("Done.")
