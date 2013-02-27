/*
 * (C) Copyright 2012 Nuxeo SA (http://nuxeo.com/) and contributors.
 *
 * All rights reserved. This program and the accompanying materials
 * are made available under the terms of the GNU Lesser General Public License
 * (LGPL) version 2.1 which accompanies this distribution, and is available at
 * http://www.gnu.org/licenses/lgpl.html
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
 * Lesser General Public License for more details.
 *
 * Contributors:
 *     Antoine Taillefer <ataillefer@nuxeo.com>
 */
package org.nuxeo.drive.hierarchy.permission.factory;

import java.security.Principal;
import java.util.Iterator;
import java.util.Map;
import java.util.Set;

import org.apache.commons.lang.StringUtils;
import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.nuxeo.common.utils.Path;
import org.nuxeo.drive.adapter.FileSystemItem;
import org.nuxeo.drive.adapter.FolderItem;
import org.nuxeo.drive.hierarchy.permission.adapter.UserSyncRootParentFolderItem;
import org.nuxeo.drive.service.FileSystemItemFactory;
import org.nuxeo.drive.service.FileSystemItemManager;
import org.nuxeo.drive.service.NuxeoDriveManager;
import org.nuxeo.drive.service.SynchronizationRoots;
import org.nuxeo.drive.service.VirtualFolderItemFactory;
import org.nuxeo.drive.service.impl.AbstractFileSystemItemFactory;
import org.nuxeo.ecm.core.api.ClientException;
import org.nuxeo.ecm.core.api.CoreSession;
import org.nuxeo.ecm.core.api.DocumentModel;
import org.nuxeo.ecm.core.api.DocumentSecurityException;
import org.nuxeo.ecm.core.api.IdRef;
import org.nuxeo.ecm.core.api.LifeCycleConstants;
import org.nuxeo.ecm.core.api.repository.RepositoryManager;
import org.nuxeo.ecm.platform.userworkspace.api.UserWorkspaceService;
import org.nuxeo.runtime.api.Framework;

/**
 * User workspace based implementation of {@link FileSystemItemFactory} for the
 * parent {@link FolderItem} of the user's synchronization roots.
 *
 * @author Antoine Taillefer
 */
public class UserSyncRootParentFactory extends AbstractFileSystemItemFactory
        implements VirtualFolderItemFactory {

    private static final Log log = LogFactory.getLog(UserSyncRootParentFactory.class);

    protected static final String FOLDER_NAME_PARAM = "folderName";

    protected String folderName;

    /*------------------- AbstractFileSystemItemFactory ------------------- */
    @Override
    public void handleParameters(Map<String, String> parameters)
            throws ClientException {
        // Look for the "folderName" parameter
        String folderNameParam = parameters.get(FOLDER_NAME_PARAM);
        if (StringUtils.isEmpty(folderNameParam)) {
            throw new ClientException(String.format(
                    "Factory %s has no %s parameter, please provide one.",
                    getName(), FOLDER_NAME_PARAM));
        }
        folderName = folderNameParam;
    }

    @Override
    public boolean isFileSystemItem(DocumentModel doc, boolean includeDeleted)
            throws ClientException {
        // Check user workspace
        Path path = doc.getPath();
        int pathLength = path.segmentCount();
        boolean isUserWorkspace = pathLength > 1
                && "UserWorkspaces".equals(path.segment(pathLength - 2));
        if (!isUserWorkspace) {
            log.debug(String.format(
                    "Document %s is not a user workspace, it cannot be adapted as a FileSystemItem.",
                    doc.getId()));
            return false;
        }
        // Check "deleted" life cycle state
        if (!includeDeleted
                && LifeCycleConstants.DELETED_STATE.equals(doc.getCurrentLifeCycleState())) {
            log.debug(String.format(
                    "Document %s is in the '%s' life cycle state, it cannot be adapted as a FileSystemItem.",
                    doc.getId(), LifeCycleConstants.DELETED_STATE));
            return false;
        }
        // Check if the user workspace is registered as a synchronization root
        // OR if the user has at least one registered synchronization root
        // he/she created
        CoreSession session = doc.getCoreSession();
        if (!(isSyncRoot(doc) || isAtLeastOneSyncRoot(session))) {
            log.debug(String.format(
                    "User workspace is not registered as a synchronization root and there are no other synchronization roots registered for user %s.",
                    session.getPrincipal().getName()));
            return false;
        }
        return true;
    }

    @Override
    protected FileSystemItem adaptDocument(DocumentModel doc,
            boolean forceParentId, String parentId) throws ClientException {
        return new UserSyncRootParentFolderItem(getName(), doc, parentId,
                folderName);
    }

    /*------------------- FileSystemItemFactory ------------------- */
    /**
     * Force parent id using {@link #getTopLevelFolderItemId(Principal)}.
     */
    @Override
    public FileSystemItem getFileSystemItem(DocumentModel doc,
            boolean includeDeleted) throws ClientException {
        Principal principal = doc.getCoreSession().getPrincipal();
        return getFileSystemItem(doc, getTopLevelFolderItemId(principal),
                includeDeleted);
    }

    /*------------------- VirtualFolderItemFactory ------------------- */
    @Override
    public FolderItem getVirtualFolderItem(Principal principal)
            throws ClientException {
        DocumentModel userWorkspace = getUserPersonalWorkspace(principal);
        return (FolderItem) getFileSystemItem(userWorkspace);
    }

    @Override
    public String getFolderName() {
        return folderName;
    }

    @Override
    public void setFolderName(String folderName) {
        this.folderName = folderName;
    }

    /*------------------- Protected ------------------- */
    protected boolean isSyncRoot(DocumentModel doc) throws ClientException {
        NuxeoDriveManager nuxeoDriveManager = Framework.getLocalService(NuxeoDriveManager.class);
        return nuxeoDriveManager.isSynchronizationRoot(
                doc.getCoreSession().getPrincipal(), doc);
    }

    protected boolean isAtLeastOneSyncRoot(CoreSession session)
            throws ClientException {
        Principal principal = session.getPrincipal();
        Map<String, SynchronizationRoots> syncRootsByRepo = Framework.getLocalService(
                NuxeoDriveManager.class).getSynchronizationRoots(principal);
        for (String repositoryName : syncRootsByRepo.keySet()) {
            Set<IdRef> syncRootRefs = syncRootsByRepo.get(repositoryName).refs;
            Iterator<IdRef> syncRootRefsIt = syncRootRefs.iterator();
            while (syncRootRefsIt.hasNext()) {
                IdRef idRef = syncRootRefsIt.next();
                try {
                    DocumentModel doc = session.getDocument(idRef);
                    // Filter by creator
                    // TODO: allow filtering by dc:creator in
                    // NuxeoDriveManager#getSynchronizationRoots(Principal
                    // principal)
                    if (principal.getName().equals(
                            doc.getPropertyValue("dc:creator"))) {
                        // TODO: should adapt doc as a FileSystemItem to go
                        // through all factory checks
                        return true;
                    }
                } catch (DocumentSecurityException e) {
                    log.debug(String.format(
                            "User %s has no READ access on synchronization root %s.",
                            principal.getName(), idRef));
                }
            }
        }
        return false;
    }

    protected String getTopLevelFolderItemId(Principal principal)
            throws ClientException {
        FolderItem topLevelFolder = getFileSystemItemManager().getTopLevelFolder(
                principal);
        if (topLevelFolder == null) {
            throw new ClientException(
                    "Found no top level folder item. Please check your contribution to the following extension point: <extension target=\"org.nuxeo.drive.service.FileSystemItemAdapterService\" point=\"topLevelFolderItemFactory\">.");
        }
        return topLevelFolder.getId();
    }

    protected DocumentModel getUserPersonalWorkspace(Principal principal)
            throws ClientException {
        UserWorkspaceService userWorkspaceService = Framework.getLocalService(UserWorkspaceService.class);
        RepositoryManager repositoryManager = Framework.getLocalService(RepositoryManager.class);
        // TODO: handle multiple repositories
        CoreSession session = getSession(
                repositoryManager.getDefaultRepository().getName(), principal);
        DocumentModel userWorkspace = userWorkspaceService.getCurrentUserPersonalWorkspace(
                session, null);
        if (userWorkspace == null) {
            throw new ClientException(String.format(
                    "No personal workspace found for user %s.",
                    principal.getName()));
        }
        return userWorkspace;
    }

    protected FileSystemItemManager getFileSystemItemManager() {
        return Framework.getLocalService(FileSystemItemManager.class);
    }

    protected CoreSession getSession(String repositoryName, Principal principal)
            throws ClientException {
        return getFileSystemItemManager().getSession(repositoryName, principal);
    }
}
