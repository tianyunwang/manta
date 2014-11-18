#! /usr/bin/env python
#
# Manta
# Copyright (c) 2013-2014 Illumina, Inc.
#
# This software is provided under the terms and conditions of the
# Illumina Open Source Software License 1.
#
# You should have received a copy of the Illumina Open Source
# Software License 1 along with this program. If not, see
# <https://github.com/sequencing/licenses/>
#


import sys
import re
from os.path import exists, isfile
from optparse import OptionParser

def getKeyVal(string,key) :
    match=re.search("%s=([^;\t]*);?" % (key) ,string)
    if match is None : return None
    return match.group(1);


VCF_CHROM = 0
VCF_POS = 1
VCF_REF = 3
VCF_ALT = 4
VCF_QUAL = 5
VCF_FILTER = 6
VCF_INFO = 7
VCF_FORMAT = 8
VCF_SAMPLE = 9

class VcfRecord :
    def __init__(self, line) :
        #self.line = line
        w = line.strip().split('\t')
        self.chrom = w[VCF_CHROM]
        self.pos = int(w[VCF_POS])
        self.isPass = (w[VCF_FILTER] == "PASS")

        self.end = self.pos+len(w[VCF_REF])-1
        val = getKeyVal(w[VCF_INFO],"END")
        if val is not None :
            self.end = int(val)

        self.svLen = None
        val = getKeyVal(w[VCF_INFO],"SVLEN")
        if val is not None :
            self.svLen = int(val)

        self.svType = getKeyVal(w[VCF_INFO],"SVTYPE")
        
        fmt = w[VCF_FORMAT]
        sample = w[VCF_SAMPLE]
        gtIx = fmt.split(':').index("GT")
        gt = sample.split(':')[gtIx]
        t = gt.split('/')
        self.gtType = int(t[0]) + int(t[1])


def getOptions():
    usage = "usage: %prog [options] vcf > filtered_vcf"
    parser = OptionParser(usage=usage)
    (options,args) = parser.parse_args()

    if len(args) == 0 :
        parser.print_help()
        sys.exit(2)

    # validate input:
    if not isfile(args[0]) :
        raise Exception("Can't find input vcf file: " +arg)

    return (options,args)


def process_block(recordBlock, nextPos, filteredSites):

    # sys.stderr.write("processing a block with %s sites...\n" % len(recordBlock))

    while (len(recordBlock) > 0):
        target = recordBlock[0]
        targetEnd = target.end
        # when a new target's end is larger than
        # the pos of the next site to be read,
        # we need to read in more sites
        if targetEnd > nextPos:
            break
        
        targetLen = -1
        if target.svLen is not None:
            targetLen = abs(target.svLen)
        targetType = target.svType

        ploidySum = target.gtType
        overlapIds = [0]
    
        for ix in xrange(1, len(recordBlock)):
            record = recordBlock[ix]
            pos = record.pos
            svLen = -1
            if record.svLen is not None:
                svLen = abs(record.svLen)
            svType = record.svType
            ploidy = record.gtType

            # collecting stacked sites
            # with the same type and similar size
            if pos < targetEnd:
                if (
                   # (svType == targetType) and
                    (svLen < 2*targetLen) and
                    (svLen > 0.5*targetLen)):
                    ploidySum += ploidy
                    overlapIds.append(ix)                    
            else:
                break

        overlapIds.reverse()
        if (ploidySum > 2):
            # sites to be filtered due to ploidity
            for i in overlapIds:
                site = recordBlock.pop(i)
                chrm = site.chrom
                pos = site.pos
                end = site.end
                
                if not(chrm in filteredSites):
                    filteredSites[chrm] = {}
                filteredSites[chrm][(pos, end)] = True
        else:
            # sites to be kept
            for i in overlapIds:
                recordBlock.pop(i)
    
    
def find_stacked_variants(vcfFile):
    filteredSites = {}
    recordBlock = []
    maxEnd = -1
    count = 0

    vcfIn = open(vcfFile)
    for line in vcfIn:
        if line[0] <> "#":
            record = VcfRecord(line)

            chrm = record.chrom
            pos = record.pos
            svType = record.svType
            count += 1

            # ignore filtered records
            isPassed = record.isPass
            if not(isPassed):
                continue
        
            # consider DEL & DUP only
            if (svType == "DEL") or (svType == "DUP"):
                end = record.end
            
                # set up the first target site
                if (len(recordBlock) == 0):
                    targetChrm = chrm
                    targetEnd = end
                else:
                    targetChrm = recordBlock[0].chrom
                    targetEnd = recordBlock[0].end

                # keep reading into the block until exceeding the target's end
                if (chrm == targetChrm) and (pos < targetEnd):
                    recordBlock.append(record)
                    maxEnd = max(maxEnd, end)
                else:
                    nextPos = pos
                    if (chrm != targetChrm):
                        nextPos = maxEnd + 1
                        maxEnd = -1
                
                    # process the block until pos < the new target's end
                    process_block(recordBlock, nextPos, filteredSites)

                    recordBlock.append(record)
                    maxEnd = max(maxEnd, end)
    vcfIn.close()
    # process the last block
    process_block(recordBlock, maxEnd+1, filteredSites)

    sys.stderr.write("Processed %s sites in the vcf.\n" % count)
    numFiltered = 0
    for c in filteredSites:
        numFiltered += len(filteredSites[c])
    sys.stderr.write("Filtered %s sites due to ploidy.\n" % numFiltered)
    sys.stderr.write("Filtered sites: %s\n" % filteredSites)
    
    return filteredSites


def check_filtered_sites(site, filteredSites):
    chrm = site.chrom
    pos = site.pos
    end = site.end

    if (chrm in filteredSites) and ((pos, end) in filteredSites[chrm]):
        return True
    else:
        return False


def filter_variants(vcfFile, filteredSites):
    
    isHeaderAdded = False
    filterHeadline = "##FILTER=<ID=Ploidy,Description=\"For DEL & DUP variants, the genotypes of overlapping variants (with similar size) leads to a ploidy larger than 2.\">\n"

    vcfIn = open(vcfFile)
    vcfOut = sys.stdout

    for line in vcfIn:
        if line[0] <> '#':
            site = VcfRecord(line)
            # only filter on DEL & DUP for now
            if (site.isPass and
                ((site.svType == "DEL") or (site.svType == "DUP"))):

                isFiltered = check_filtered_sites(site, filteredSites)
                if isFiltered:
                    w = line.strip().split('\t')
                    # add the "Ploidy" filter
                    w[VCF_FILTER] = "Ploidy"
                    line = w[0]
                    for i in xrange(1, len(w)):
                        line += "\t"+w[i]
                    line += "\n"
        elif not(isHeaderAdded) and (line[:8] == "##FILTER"):
            vcfOut.write(filterHeadline)
            isHeaderAdded = True
            
        vcfOut.write(line)
    vcfIn.close()
        

if __name__=='__main__':

    # Command-line args
    (options,args) = getOptions()
    vcfFile = args[0]

    filteredSites = find_stacked_variants(vcfFile)
    filter_variants(vcfFile, filteredSites)
