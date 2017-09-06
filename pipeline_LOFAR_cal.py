#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys, os, glob, re

import numpy as np

import lib_log, lib_ms, lib_util


# Temporary!
parset_dir = "/home/fdg/scripts/autocal/parset_cal"
skymodel   = "/home/fdg/scripts/model/calib-simple.skymodel"
imaging    = False

if 'tooth' in os.getcwd(): # tooth 2013
    datadir = '../cals-bkp/'
    bl2flag = 'CS031LBA'
elif 'bootes' in os.getcwd(): # bootes 2013
    datadir = '../cals-bkp/'
    bl2flag = 'CS013LBA\;CS031LBA'
elif 'survey' in os.getcwd():
    obs     = os.getcwd().split('/')[-2] # assumes .../c??-o??/3c196
    calname = os.getcwd().split('/')[-1] # assumes .../c??-o??/3c196
    datadir = '../../download/%s/%s' % (obs, calname)
    bl2flag = 'CS031LBA\;RS310LBA\;RS210LBA\;RS409LBA'
    if 'c07-o00' in os.getcwd() or 'c07-o01' in os.getcwd() or 'c07-o02' in os.getcwd() or 'c07-o03' in os.getcwd() or 'c07-o04' in os.getcwd() or 'c07-o05' in os.getcwd() or 'c07-o06' in os.getcwd():
        bl2flag = 'CS031LBA\;RS310LBA\;RS210LBA\;RS409LBA\;RS407LBA'
else:
    datadir = '../cals-bkp/'
    bl2flag = ''

########################################################
logger  = lib_log.set_logger('pipeline-cal.logger')
lib_util.check_rm('logs')
s       = lib_util.Scheduler(dry = False)
MSs     = lib_ms.AllMSs(glob.glob(datadir+'/*MS'))
calname = MSs.get_list_obj[0].get_calname()

if (calname == 'CygA'):
    sourcedb = "/home/fdg/scripts/model/A-team_4_CC.skydb"
else:
    sourcedb = "/home/fdg/scripts/model/calib-simple.skydb"

############################################################
logger.info('Copy data...')
for ms in MSs.get_list_str():
    msout = ms.split('/')[-1]
    if os.path.exists(msout): continue
    s.add('DPPP ' + parset_dir + '/DPPP_avg.parset msin='+ms+' msout='+msout+' msin.datacolumn=DATA avg.timestep=1 avg.freqstep=1', \
                log=msout+'_cp.log', commandType = "DPPP") # better than cp as activates dysco
s.run(check=True)

MSs = lib_ms.AllMSs( glob.glob('./*MS'), s )

############################################################
# flag bad stations, flags will propagate
logger.info("Flagging...")
MSs.run("DPPP " + parset_dir + "/DPPP_flag.parset msin=$ms flag1.baseline=" + bl2flag, log="$ms_flag.log", commandType = "DPPP")

# predict to save time ms:MODEL_DATA
logger.info('Predict...')
MSs.run("DPPP " + parset_dir + "/DPPP_predict.parset msin=$ms pre.sourcedb=" + sourcedb + " pre.sources=" + calname, log = "$ms_pre.log", commandType = "DPPP")

##################################################
# 1: find the FR and remove it

# Beam correction DATA -> CORRECTED_DATA
logger.info('Beam correction...')
MSs.run("DPPP " + parset_dir + '/DPPP_LOFAR_beam.parset msin=$ms', log='$ms_beam.log', commandType = "DPPP")

# Convert to circular CORRECTED_DATA -> CORRECTED_DATA
logger.info('Converting to circular...')
MSs.run('mslin2circ.py -i $ms:CORRECTED_DATA -o $ms:CORRECTED_DATA', log='$ms_circ2lin.log', commandType ='python')

# Smooth data CORRECTED_DATA -> SMOOTHED_DATA (BL-based smoothing)
logger.info('BL-smooth...')
MSs.run('BLsmooth.py -r -i CORRECTED_DATA -o SMOOTHED_DATA $ms', log='$ms_smooth1.log', commandType ='python')

# Solve cal_SB.MS:SMOOTHED_DATA (only solve)
logger.info('Calibrating...')
for MS in MSs.get_list_str():
    lib_util.check_rm(ms+'/instrument')
MSs.run('NDPPP ' + parset_dir + '/DPPP_LOFAR_sol.parset msin=$ms', log='$ms_sol1.log', commandType = "DPPP")

# TODO: add losoto concat
run_losoto(s, 'fr', MSs.get_list_str(), [parset_dir + '/losoto-fr.parset'], outtab='rotationmeasure000', \
    inglobaldb='globaldb', outglobaldb='globaldb-fr', ininstrument='instrument', outinstrument='instrument-fr', putback=True)

#####################################################
# 2: find amplitude + cd

# Beam correction DATA -> CORRECTED_DATA
logger.info('Beam correction...')
MSs.run('NDPPP ' + parset_dir + '/DPPP_LOFAR_beam.parset msin=$ms', log='$ms_beam.log', commandType = "DPPP")

# Correct FR CORRECTED_DATA -> CORRECTED_DATA
logger.info('Faraday rotation correction...')
MSs.run('NDPPP ' + parset_dir + '/DPPP_LOFAR_cor.parset msin=$ms cor.parmdb=$ms/instrument-fr cor.correction=rotationmeasure', log='$ms_corFR.log', commandType = "DPPP")

# Smooth data CORRECTED_DATA -> SMOOTHED_DATA (BL-based smoothing)
logger.info('BL-smooth...')
MSs.run('BLsmooth.py -r -i CORRECTED_DATA -o SMOOTHED_DATA $ms', log='$ms_smooth2.log', commandType ='python')

# Solve cal_SB.MS:SMOOTHED_DATA (only solve)
logger.info('Calibrating...')
for MS in MSs.get_list_str():
    lib_util.check_rm(ms+'/instrument')
MSs.run('NDPPP ' + parset_dir + '/DPPP_sol.parset msin=$ms', log='$ms_sol2.log', commandType = "DPPP")

run_losoto(s, 'cd', MSs.get_list_str(), [parset_dir + '/losoto-flag.parset',parset_dir+'/losoto-amp.parset',parset_dir+'/losoto-cd.parset'], outtab='amplitudeSmooth000,crossdelay', \
    inglobaldb='globaldb', outglobaldb='globaldb', ininstrument='instrument', outinstrument='instrument-cd', putback=True)

#################################################
# 3: find iono

# Correct cd+amp DATA -> CORRECTED_DATA
logger.info('Cross delay+ampBP correction...')
MSs.run('NDPPP '+parset_dir+'/DPPP_cor.parset msin=$ms msin.datacolumn=DATA cor.parmdb=$ms/instrument-cd cor.correction=gain cor.updateweights=True', log='$ms_corCD.log', commandType = "DPPP")

# Beam correction (and update weight in case of imaging) CORRECTED_DATA -> CORRECTED_DATA
logger.info('Beam correction...')
MSs.run('NDPPP '+parset_dir+'/DPPP_beam.parset msin=$ms msin.datacolumn=CORRECTED_DATA corrbeam.updateweights=True', log='$ms_beam2.log', commandType = "DPPP")

# Correct FR CORRECTED_DATA -> CORRECTED_DATA
logger.info('Faraday rotation correction...')
MSs.run('NDPPP '+parset_dir+'/DPPP_cor.parset msin=$ms cor.parmdb=$ms/instrument-fr cor.correction=rotationmeasure', log='$ms_corFR2.log', commandType = "DPPP")

# Smooth data CORRECTED_DATA -> SMOOTHED_DATA (BL-based smoothing)
logger.info('BL-smooth...')
MSs.run('BLsmooth.py -r -i CORRECTED_DATA -o SMOOTHED_DATA $ms', log='$ms_smooth3.log', commandType ='python')

# Solve cal_SB.MS:SMOOTHED_DATA (only solve)
logger.info('Calibrating...')
for MS in MSs.get_list_str():
    lib_util.check_rm(ms+'/instrument')
MSs.run('NDPPP '+parset_dir+'/DPPP_sol.parset msin=$ms', log='$ms_sol3.log', commandType = "DPPP")

# if field model available, subtract it
field_model = '/home/fdg/scripts/model/calfields/'+calname+'-field.skydb'
if os.path.exists(field_model):
    logger.info('Removing field sources...')

    run_losoto(s, 'noamp', MSs.get_list_str(), [parset_dir+'/losoto-noamp.parset'], outtab='amplitude000,phase000', \
           inglobaldb='globaldb', outglobaldb='globaldb', ininstrument='instrument', outinstrument='instrument', putback=True)

    logger.info('Ft+corrupt model...')
    MSs.run('NDPPP '+parset_dir+'/DPPP_predict.parset msin=$ms pre.sourcedb='+field_model+' \
                pre.applycal.parmdb=$ms/instrument pre.applycal.correction=gain', log='$ms_field_pre.log', commandType = "DPPP")

    logger.info('Subtract model...')
    MSs.run('taql "update $ms set CORRECTED_DATA = CORRECTED_DATA - MODEL_DATA"', log='$ms_field_taql.log', commandType ='general')

    # Smooth data CORRECTED_DATA -> SMOOTHED_DATA (BL-based smoothing)
    logger.info('BL-smooth...')
    MSs.run('BLsmooth.py -r -i CORRECTED_DATA -o SMOOTHED_DATA $ms', log='$ms_field_smooth.log', commandType ='python')

    # Solve cal_SB.MS:SMOOTHED_DATA (only solve)
    logger.info('Calibrating...')
    for MS in MSs.get_list_str():
        lib_util.check_rm(ms+'/instrument')
    MSs.run('NDPPP '+parset_dir+'/DPPP_sol.parset msin=$ms', log='$ms_field_sol.log', commandType = "DPPP")

    run_losoto(s, 'iono', MSs.get_list_str(), [parset_dir+'/losoto_iono.parset'], outtab='amplitude000,phaseOrig000', \
           inglobaldb='globaldb', outglobaldb='globaldb', ininstrument='instrument', outinstrument='instrument', putback=True)

# copy amp from cd h5parm to iono h5parm
logger.info('Prepare final globaldb...')
s.add('H5parm_merge.py cal-cd.h5:sol000 cal-iono.h5:solcd', log='losoto-iono.log', log_append=True, cmd_type = 'python', processors='max')
s.run(check = True)

s.add('losoto -v cal-iono.h5 '+parset_dir+'/losoto_dup.parset', log='losoto-iono.log', log_append=True, cmd_type = 'python', processors='max')
s.run(check = True)

s.add('H5parm_exporter.py -v -c --soltab amplitudeSmooth000,phaseOrig000 cal-iono.h5 globaldb', log='losoto-iono.log', log_append=True, commandType ='python', processors='max')
s.run(check = True)

if 'survey' in os.getcwd():
    lib_util.check_rm('globaldb/instrument*') # keep only filled instrument tables
    newglobaldb = 'globaldb_'+os.getcwd().split('/')[-2]
    logger.info('Copy: globaldb -> dsk:/disks/paradata/fdg/LBAsurvey/%s' % newglobaldb)
    os.system('ssh dsk "rm -rf /disks/paradata/fdg/LBAsurvey/%s"' % newglobaldb)
    os.system('scp -q -r globaldb dsk:/disks/paradata/fdg/LBAsurvey/%s' % newglobaldb)

# a debug image
if imaging:

    from make_mask import make_mask
    if not 'survey' in os.getcwd():
        MSs = AllMSs( glob.glob('./*MS')[int(len(glob.glob('./*MS'))/2.):] ) # keep only upper band

    # Correct all CORRECTED_DATA (beam, CD, FR, BP corrected) -> CORRECTED_DATA
    logger.info('Amp/ph correction...')
    MSs.run("DPPP " + parset_dir + '/DPPP_cor.parset msin=$ms cor.parmdb=$ms/instrument cor.correction=gain', log='$ms_corG.log', commandType = "DPPP")

    logger.info('Subtract model...')
    MSs.run('taql "update $ms set CORRECTED_DATA = CORRECTED_DATA - MODEL_DATA"', log='$ms_taql2.log', commandType ='general')

    logger.info('Cleaning...')
    lib_util.check_rm('img')
    os.makedirs('img')
    imagename = 'img/wide'
    s.add('wsclean -reorder -name ' + imagename + ' -size 5000 5000 -trim 4000 4000 -mem 90 -j '+str(s.max_processors)+' -baseline-averaging 2.0 \
            -scale 5arcsec -weight briggs 0.0 -niter 100000 -no-update-model-required -mgain 0.9 \
            -pol I -joinchannels -fit-spectral-pol 2 -channelsout 10 -auto-threshold 20 '+MSs.get_str_wsclean(), \
            log='wscleanA.log', commandType ='wsclean', processors='max')
    s.run(check = True)

    # make mask
    maskname = imagename+'-mask.fits'
    make_mask(image_name = imagename+'-MFS-image.fits', mask_name = maskname, threshisl = 3, atrous_do=True)
    # remove CC not in mask
    for modelname in sorted(glob.glob(imagename+'*model.fits')):
        blank_image_fits(modelname, maskname, inverse=True)

    logger.info('Cleaning w/ mask')
    imagename = 'img/wideM'
    s.add('wsclean -reorder -name ' + imagename + ' -size 5000 5000 -trim 4000 4000 -mem 90 -j '+str(s.max_processors)+' -baseline-averaging 2.0 \
            -scale 5arcsec -weight briggs 0.0 -niter 100000 -no-update-model-required -mgain 0.8 -minuv-l 100 \
            -pol I -joinchannels -fit-spectral-pol 2 -channelsout 10 -auto-threshold 0.1 -save-source-list -apply-primary-beam -use-differential-lofar-beam \
            -fitsmask '+maskname+' '+MSs.get_str_wsclean(), \
            log='wscleanB.log', commandType = 'wsclean', processors = 'max')
    s.run(check = True)

    # prepare mask
    logger.info('Masking skymodel...')
    make_mask(image_name=imagename+'-MFS-image.fits', mask_name=imagename+'-mask.fits', threshisl=5, atrous_do=True)
    # apply mask
    logger.info('Predict (apply mask)...')
    lsm = lsmtool.load(imagename+'-sources-pb.txt')
    lsm.select('%s == True' % (imagename+'-mask.fits'))
    cRA, cDEC = get_phase_centre(MSs[0])
    lsm.select( lsm.getDistance(cRA, cDEC) > 0.1 )
    lsm.group('every')
    lsm.write(imagename+'-sources-pb-cut.txt', format='makesourcedb', clobber=True)
    del lsm

logger.info("Done.")