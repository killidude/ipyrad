#!/usr/bin/env ipython2

""" demultiplex raw sequence data given a barcode map."""

from __future__ import print_function
# pylint: disable=E1101
import os
import gzip
import glob
import tempfile
import itertools
import numpy as np
import cPickle as pickle
from ipyrad.core.sample import Sample
from .util import *
from collections import defaultdict, Counter

import logging
LOGGER = logging.getLogger(__name__)


def combinefiles(filepath):
    """ Joins first and second read file names """
    ## unpack seq files in filepath
    fastqs = glob.glob(filepath)
    firsts = [i for i in fastqs if "_R1_" in i]

    ## check names
    if not firsts:
        raise AssertionError("First read files names must contain '_R1_'.")

    ## get paired reads
    seconds = [ff.replace("_R1_", "_R2_") for ff in firsts]
    return zip(firsts, seconds)


def matching(barcode, data):
    "allows for N base difference between barcodes"
    LOGGER.debug("in matching")
    match = 0
    for name, realbar in data.barcodes.items():
        if len(barcode) == len(realbar):
            sames = sum([i == j for (i, j) in zip(barcode, realbar)])
            diffs = len(barcode) - sames
            if diffs <= data.paramsdict["max_barcode_mismatch"]:
                match = name
    return match


def findbcode(cut, longbar, read1):
    """ find barcode sequence in the beginning of read """
    LOGGER.debug("in findbcode. cut %s, longbar %s", cut, longbar)
    ## default barcode string
    search = read1[1][:int(longbar[0]+len(cut)+1)]
    countcuts = search.count(cut)
    if countcuts == 1:
        barcode = search.split(cut, 1)[0]
    elif countcuts == 2:
        barcode = search.rsplit(cut, 2)[0]
    else:
        barcode = ""
    LOGGER.debug("search_: %s", search)
    LOGGER.debug("barcode: %s", barcode)
    return barcode




def barmatch(args):
    """
    Matches reads to barcodes in barcode file and writes to individual temp 
    files, after all read files have been split, temp files are collated into 
    .fastq files
    """

    ## read in list of args
    data, rawfile, chunk, cut, longbar, chunknum, filenum = args

    ## counters for stats output
    total = 0
    cutfound = 0      ## cut site found
    matched = 0       ## bar matches 

    ## dictionary to record barcode hits & misses
    samplehits = {}
    barhits = {}
    misses = {}
    misses['_'] = 0
    
    ## read in paired end read files"
    fr1 = open(chunk[0], 'rb')
    ## create iterators to sample 4 lines at a time
    quart1 = itertools.izip(*[iter(fr1)]*4)
    if 'pair' in data.paramsdict["datatype"]:
        fr2 = open(chunk[1], 'rb')
        quart2 = itertools.izip(*[iter(fr2)]*4)
        quarts = itertools.izip(quart1, quart2)
    else:
        ## read in single end read file"
        quarts = itertools.izip(quart1, iter(int, 1))

    ## dictionaries to store first and second reads
    dsort1 = defaultdict(list)
    dsort2 = defaultdict(list)
    dbars = defaultdict(list)    ## all bars matched in sample
    for sample in data.barcodes:
        dsort1[sample] = []
        dsort2[sample] = []
        dbars[sample] = set()

    ## get func for finding barcode
    LOGGER.debug("longbar: %s", longbar)
    if longbar[1] == 'same':
        if data.paramsdict["datatype"] == '2brad':
            def getbarcode(_, read1, longbar):
                """ find barcode for 2bRAD data """
                return read1[1][-longbar[0]:]
        else:
            def getbarcode(_, read1, longbar):
                """ finds barcode for invariable length barcode data """
                return read1[1][:longbar[0]]
    else:
        def getbarcode(cut, read1, longbar):
            """ finds barcode for variable barcode lengths"""
            return findbcode(cut, longbar, read1)

    ## go until end of the file
    while 1:
        try: 
            read1s, read2s = quarts.next()
        except StopIteration: 
            break
        total += 1

        ## strip
        read1 = np.array([i.strip() for i in read1s])
        if 'pair' in data.paramsdict["datatype"]:
            read2 = np.array([i.strip() for i in read2s])

        ## Parse barcode from sequence 
        ## very simple sorting if barcodes are invariable
        barcode = getbarcode(cut, read1, longbar)

        ## find if it matches 
        didmatch = matching(barcode, data)
        if didmatch:
            dbars[didmatch].add(barcode)
            matched += 1
            cutfound += 1
            ## trim off barcode
            if data.paramsdict["datatype"] == '2brad':
                read1[1] = read1[1][:-len(barcode)]
                read1[3] = read1[3][:-len(barcode)]
            else:
                read1[1] = read1[1][len(barcode):]
                read1[3] = read1[3][len(barcode):]

            ## record who matched
            if didmatch in samplehits:
                samplehits[didmatch] += 1
            else:
                samplehits[didmatch] = 1

            if barcode in barhits:
                barhits[barcode] += 1
            else:
                barhits[barcode] = 1                
            ## append to dsort
            dsort1[didmatch].append("\n".join(read1).strip())
            if 'pair' in data.paramsdict["datatype"]:
                dsort2[didmatch].append("\n".join(read2).strip())

        else:
            ## record whether cut found                
            if barcode:
                cutfound += 1
                if barcode in misses:
                    misses[barcode] += 1
                else:
                    misses[barcode] = 1
            else:
                misses["_"] += 1

        ## write out at 10K to keep memory low
        if not total % 10000:
            ## write the remaining reads to file"
            writetofile(data, dsort1, 1, filenum, chunknum)
            if 'pair' in data.paramsdict["datatype"]:
                writetofile(data, dsort2, 2, filenum, chunknum) 
            ## clear out dsorts
            for sample in data.barcodes:
                dsort1[sample] = []
                dsort2[sample] = []

    ## write the remaining reads to file"
    writetofile(data, dsort1, 1, filenum, chunknum)
    if 'pair' in data.paramsdict["datatype"]:
        writetofile(data, dsort2, 2, filenum, chunknum)        

    ## close file handles
    fr1.close()
    if 'pair' in data.paramsdict["datatype"]:
        fr2.close()

    ## return stats in saved pickle b/c return_queue is too tiny
    handle = os.path.splitext(os.path.basename(rawfile))[0]
    filestats = [handle, total, cutfound, matched]
    samplestats = [samplehits, barhits, misses, dbars]

    pickout = open(os.path.join(
                      data.dirs.fastqs,
                      handle+"_"+str(chunknum)+"_"+\
                      str(filenum)+".pickle"), "wb")
    pickle.dump([filestats, samplestats], pickout)
    pickout.close()

    return "done"



def writetofile(data, dsort, read, filenum, chunknum, mode='a'):
    """ writes dsort dict to a tmp file. Used in barmatch. """
    if read == 1:
        rname = "_R1_"
    else:
        rname = "_R2_"

    for sample in dsort:
        ## skip writing if empty
        if dsort[sample]:
            handle = os.path.join(data.dirs.fastqs,
                             "tmp_"+sample+rname+str(filenum)+"_"+str(chunknum))
            with open(handle, mode) as out:
                out.write("\n".join(dsort[sample])+"\n")



def maketempfiles(data, chunk1, chunk2):
    """ writes data chunks to temp files """

    with tempfile.NamedTemporaryFile('w+b', delete=False, 
         dir=os.path.realpath(data.dirs.fastqs), 
         prefix="tmp_", suffix=".chunk") as out1:
        out1.write("".join(chunk1))

    out2 = tempfile.NamedTemporaryFile("w+b", delete=False, 
           dir=os.path.realpath(data.dirs.fastqs), 
           prefix="tmp_", suffix=".chunk")
    if chunk2:
        out2.write("".join(chunk2))            
    out2.close()
    del chunk1
    del chunk2
    return (out1.name, out2.name)




def parallel_chunker(data, raws, ipyclient):
    """ iterate over raw data files and split into N pieces. This is 
    parallelized across N files, so works faster if there are multiple input 
    files. """
    ## count how many rawfiles have been done
    num = 0
    submitted_args = []
    for num, rawtuple in enumerate(list(raws)):
        submitted_args.append([data, rawtuple, num, 0])
        num += 1

    ## call to ipp
    lbview = ipyclient.load_balanced_view()
    results = lbview.map_async(zcat_make_temps, submitted_args)
    datatuples = results.get()
    del lbview

    return datatuples



def parallel_sorter(data, rawtups, chunks, cutter, longbar, filenum, ipyclient):
    """ takes list of chunk files and runs barmatch function on them across
    all engines and outputs temp file results. This is parallelized on N chunks.
    """
    LOGGER.debug("in parallel_sorter") 
    ## send file to multiprocess queue"
    chunknum = 0
    submitted_args = []
    for tmptuple in chunks:
        submitted_args.append([data, rawtups, tmptuple, cutter,
                               longbar, chunknum, filenum])
        chunknum += 1

    lbview = ipyclient.load_balanced_view()
    results = lbview.map_async(barmatch, submitted_args)
    results.get()
    del lbview
 


def parallel_collate(data, ipyclient):
    """ parallel calls to collate_tmps function """
    ## send file to multiprocess queue"
    LOGGER.debug("in parallel_collate")
    submitted_args = []
    for samplename in data.barcodes.keys():
        submitted_args.append([data, samplename])

    lbview = ipyclient.load_balanced_view()
    results = lbview.map_async(collate_tmps, submitted_args)
    results.get()
    del lbview



def collate_tmps(args):
    """ collate temp files back into 1 sample """
    ## split args
    data, name = args

    ## nproc len list of chunks
    combs = glob.glob(os.path.join(
                      data.dirs.fastqs, "tmp_"+name)+"_R1_*")
    combs.sort(key=lambda x: int(x.split("_")[-1][0]))

    ## one outfile to write to
    handle_r1 = os.path.join(data.dirs.fastqs, name+"_R1_.fastq.gz")
    with gzip.open(handle_r1, 'wb') as out:
        for fname in combs:
            with open(fname) as infile:
                out.write(infile.read())
   
    if "pair" in data.paramsdict["datatype"]:
        ## nproc len list of chunks
        combs = glob.glob(os.path.join(
                          data.dirs.fastqs, "tmp_"+name)+"_R2_*")
        combs.sort()                        
        ## one outfile to write to
        handle_r2 = os.path.join(data.dirs.fastqs, name+"_R2_.fastq.gz")
        with gzip.open(handle_r2, 'wb') as out:
            for fname in combs:
                with open(fname) as infile:
                    out.write(infile.read())



def prechecks(data):
    """ todo before starting analysis """
    ## check for data
    assert glob.glob(data.paramsdict["raw_fastq_path"]), \
        "No data found in {}. Fix path to data files".\
        format(data.paramsdict["raw_fastq_path"])

    ## find longest barcode
    barlens = [len(i) for i in data.barcodes.values()]
    if len(set(barlens)) == 1:
        longbar = (barlens[0], 'same')
    else:
        longbar = (max(barlens), 'diff')

    ## make sure there is a [workdir] and a [workdir/name_fastqs]
    data.dirs.fastqs = os.path.join(data.paramsdict["working_directory"],
                                    data.name+"_fastqs")
    if not os.path.exists(data.paramsdict["working_directory"]):
        os.mkdir(data.paramsdict["working_directory"])
    if not os.path.exists(data.dirs.fastqs):
        os.mkdir(data.dirs.fastqs)

    ## if leftover tmp files, remove
    oldtmps = glob.glob(os.path.join(data.dirs.fastqs, "tmp_*.gz"))
    for oldtmp in oldtmps:
        os.remove(oldtmp)

    ## gather raw sequence data
    if "pair" in data.paramsdict["datatype"]:
        raws = combinefiles(data.paramsdict["raw_fastq_path"])
    else:
        raws = zip(glob.glob(data.paramsdict["raw_fastq_path"]), iter(int, 1))

    ## make list of all perfect matching cut sites
    cut1, _ = [ambigcutters(i) for i in \
                   data.paramsdict["restriction_overhang"]][0]
    assert cut1, "Must have a restriction overhang entered for demultiplexing."

    return raws, longbar, cut1



def make_stats(data, raws):
    """ reads in pickled stats, collates, and writes to file """
    ## stats for each rawdata file
    perfile = {}
    for rawtuple in raws:
        handle = os.path.splitext(os.path.basename(rawtuple[0]))[0]
        perfile[handle] = {}
        perfile[handle]["ftotal"] = 0
        perfile[handle]["fcutfound"] = 0
        perfile[handle]["fmatched"] = 0

    ## stats for each sample
    fdbars = {}
    fsamplehits = Counter()
    fbarhits = Counter()
    fmisses = Counter()

    ## get stats from each file pickle
    pickles = glob.glob(os.path.join(data.dirs.fastqs, "*.pickle"))
    for picfile in pickles:
        with open(picfile, "rb") as pickin:
            filestats, samplestats = pickle.load(pickin)

        #counts = [total, cutfound, matched]
        handle, total, cutfound, matched = filestats
        samplehits, barhits, misses, dbars = samplestats

        ## update file stats
        perfile[handle]["ftotal"] += total
        perfile[handle]["fcutfound"] += cutfound
        perfile[handle]["fmatched"] += matched    

        ## update sample stats
        fsamplehits.update(samplehits)
        fbarhits.update(barhits)        
        fmisses.update(misses)
        fdbars.update(dbars)


    data.statsfiles.s1 = os.path.join(data.dirs.fastqs, 
                                      's1_demultiplex_stats.txt')
    outfile = open(data.statsfiles.s1, 'w')

    ## how many from each rawfile
    outfile.write('{:<35}  {:>13}{:>13}{:>13}\n'.\
                  format("raw_file", "total_reads", 
                         "cut_found", "bar_matched"))
    ## sort rawfile names
    rawfilenames = sorted(perfile)
    for rawstat in rawfilenames:
        dat = [perfile[rawstat][i] for i in ["ftotal", "fcutfound", "fmatched"]]
        outfile.write('{:<35}  {:>13}{:>13}{:>13}\n'.\
            format(*[rawstat]+[str(i) for i in dat]))
        if "pair" in data.paramsdict["datatype"]:
            rawstat2 = rawstat.replace("_R1_", "_R2_")
            outfile.write('{:<35}  {:>13}{:>13}{:>13}\n'.\
                format(*[rawstat2]+[str(i) for i in dat]))

    ## spacer, how many records for each sample
    outfile.write('\n{:<35}  {:>13}\n'.\
                  format("sample_name", "total_R1_reads"))

    ## names alphabetical
    names_sorted = sorted(data.barcodes)
    for name in names_sorted:
        outfile.write("{:<35}  {:>13}\n".format(name, fsamplehits[name]))

    ## spacer, which barcodes were found
    outfile.write('\n{:<35}  {:>13}{:>13}{:>13}\n'.\
                  format("sample_name", "true_bar", "obs_bar", "N_records"))

    ## write sample results
    for name in names_sorted:
        ## write perfect hit
        hit = data.barcodes[name]
        outfile.write('{:<35}  {:>13}{:>13}{:>13}\n'.\
            format(name, hit, hit, fsamplehits[name]))

        ## write off-n hits
        ## sort list of off-n hits
        if name in fdbars:
            offkeys = list(fdbars.get(name))
            offkeys.sort(key=fbarhits.get)
            for offhit in offkeys[::-1]:
                ## exclude perfect hit
                if offhit not in data.barcodes.values():
                    outfile.write('{:<35}  {:>13}{:>13}{:>13}\n'.\
                        format(name, hit, offhit, fbarhits[offhit]))

    ## write misses
    misskeys = list(fmisses.keys())
    misskeys.sort(key=fmisses.get)
    for key in misskeys[::-1]:
        outfile.write('{:<35}  {:>13}{:>13}{:>13}\n'.\
            format("no_match", "_", key, fmisses[key]))

    outfile.close()

    ## Link Sample with this data file to the Assembly object
    for name in data.barcodes:
        sample = Sample()
        sample.name = name
        sample.barcode = data.barcodes[name]
        if "pair" in data.paramsdict["datatype"]:
            sample.files.fastqs = [(os.path.join(data.dirs.fastqs,
                                                  name+"_R1_.fastq.gz"),
                                     os.path.join(data.dirs.fastqs,
                                                  name+"_R2_.fastq.gz"))]
        else:
            sample.files.fastqs = [(os.path.join(data.dirs.fastqs,
                                                  name+"_R1_.fastq.gz"),)]
        sample.stats["reads_raw"] = fsamplehits[name]
        if sample.stats["reads_raw"]:
            sample.stats.state = 1
            data.samples[sample.name] = sample
        else:
            print("Excluded sample: no data found for", name)



def run(data, preview, ipyclient):
    """ demultiplexes raw fastq files given a barcodes file"""

    ## checks on data before starting
    raws, longbar, cut1 = prechecks(data)

    ## nested structure to prevent abandoned temp files
    try: 
        ## splits up all files into chunks, returns list of list
        ## of chunks names in tuples
        datatuples = parallel_chunker(data, raws, ipyclient) 

        if preview:
            LOGGER.warn(\
        "Running preview mode. Selecting subset of data for demultiplexing.")
            datatuples = [(datatuples[0][0], datatuples[0][1])]

        filenum = 0            
        for rawfilename, chunks in datatuples:
            for cutter in cut1:
                if cutter:     
                    ## sort chunks for this list     
                    parallel_sorter(data, rawfilename, chunks, cutter,
                                    longbar, filenum, ipyclient)
            filenum += 1
            ## TODO: combine tmps for ambiguous cuts
            ## ...
        ## collate tmps back into one file
        parallel_collate(data, ipyclient)
        #collate_tmps(data, paired)
        make_stats(data, raws)

    finally:
        ## cleans up chunk files and stats pickles
        tmpfiles = glob.glob(os.path.join(data.dirs.fastqs, "chunk*"))        
        tmpfiles += glob.glob(os.path.join(data.dirs.fastqs, "tmp_*.gz"))
        tmpfiles += glob.glob(os.path.join(data.dirs.fastqs, "*.pickle"))
        if tmpfiles:
            for tmpfile in tmpfiles:
                os.remove(tmpfile)



if __name__ == "__main__":

    ## run test
    import ipyrad as ip
    #from ipyrad.core.assembly import Assembly

    ## get current location
    PATH = os.path.abspath(os.path.dirname(__file__))
    ## get location of test files
    IPATH = os.path.dirname(os.path.dirname(PATH))
    
    DATA = os.path.join(IPATH, "tests", "test_rad")
    TEST = ip.Assembly("test-demultiplex")
    #TEST = ip.load_assembly(os.path.join(DATA, "testrad"))
    TEST.set_params(1, "./")
    TEST.set_params(2, "./tests/data/sim_rad_test_R1_.fastq.gz")
    TEST.set_params(3, "./tests/data/sim_rad_test_barcodes.txt")
    TEST.step1()
